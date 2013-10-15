# test_smoke.py -- Functional tests for the Swift backend.
# Copyright (C) 2013 eNovance SAS <licensing@enovance.com>
#
# Author: Fabien Boucher <fabien.boucher@enovance.com>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; version 2
# of the License or (at your option) any later version of
# the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
# MA  02110-1301, USA.

import os
import unittest
import threading
import tempfile
import shutil

from dulwich import server
from dulwich import swift
from dulwich import repo
from dulwich import index
from dulwich import client
from dulwich import objects


"""Start functional tests

A Swift installation must be available before
starting those tests. The account and authentication method used
during this functional can be changed in the configuration file
passed as environment variable.
The container used to create a fake repository is defined
in cls.fakerepo and will be deleted after the tests.

DULWICH_SWIFT_CFG=/tmp/conf.cfg PYTHONPATH=. python -m unittest \
    dulwich.tests.functional_swift_tests.test_smoke
"""


class DulwichServer(threading.Thread):
    """Start the TCPGitServer with Swift backend
    """
    def __init__(self, backend, port):
        self.port = port
        self.backend = backend
        super(DulwichServer, self).__init__()

    def run(self):
        self.server = server.TCPGitServer(self.backend,
                                          'localhost',
                                          port=self.port)
        self.server.serve_forever()


class SwiftSystemBackend(server.Backend):

    def open_repository(self, path):
        return swift.SwiftRepo(path)


class SwiftRepoSmokeTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.backend = SwiftSystemBackend()
        cls.port = 9418
        cls.server_address = 'localhost'
        cls.fakerepo = 'fakerepo'
        cls.th_server = DulwichServer(cls.backend, cls.port)
        cls.th_server.start()

    @classmethod
    def tearDownClass(cls):
        cls.th_server.server.shutdown()
        cls.th_server.join()

    def setUp(self):
        self.scon = swift.SwiftConnector(self.fakerepo)
        if self.scon.test_root_exists():
            self.scon.del_root()
        self.temp_d = tempfile.mkdtemp()
        if os.path.isdir(self.temp_d):
            shutil.rmtree(self.temp_d)

    def tearDown(self):
        if self.scon.test_root_exists():
            self.scon.del_root()
        if os.path.isdir(self.temp_d):
            shutil.rmtree(self.temp_d)

    def test_init_bare(self):
        swift.SwiftRepo.init_bare(self.scon)
        self.assertTrue(self.scon.test_root_exists())
        obj = self.scon.get_container_objects()
        filtered = [o for o in obj if o['name'] == 'info/refs'
                    or o['name'] == 'objects/pack']
        self.assertEqual(len(filtered), 2)

    def test_clone_bare(self):
        local_repo = repo.Repo.init(self.temp_d, mkdir=True)
        swift.SwiftRepo.init_bare(self.scon)
        tcp_client = client.TCPGitClient(self.server_address,
                                         port=self.port)
        remote_refs = tcp_client.fetch(self.fakerepo, local_repo)
        # The remote repo is empty (no refs retreived)
        self.assertEqual(remote_refs, None)

    def test_push_commit(self):
        def determine_wants(*args):
            return {"refs/heads/master": local_repo.refs["HEAD"]}

        local_repo = repo.Repo.init(self.temp_d, mkdir=True)
        # Nothing in the staging area
        local_repo.do_commit('Test commit', 'fbo@localhost')
        sha = local_repo.refs.read_loose_ref('refs/heads/master')
        swift.SwiftRepo.init_bare(self.scon)
        tcp_client = client.TCPGitClient(self.server_address,
                                         port=self.port)
        tcp_client.send_pack(self.fakerepo,
                             determine_wants,
                             local_repo.object_store.generate_pack_contents)
        swift_repo = swift.SwiftRepo("fakerepo")
        remote_sha = swift_repo.refs.read_loose_ref('refs/heads/master')
        self.assertEqual(sha, remote_sha)

    def test_push_branch(self):
        def determine_wants(*args):
            return {"refs/heads/mybranch":
                    local_repo.refs["refs/heads/mybranch"]}

        local_repo = repo.Repo.init(self.temp_d, mkdir=True)
        # Nothing in the staging area
        local_repo.do_commit('Test commit', 'fbo@localhost',
                             ref='refs/heads/mybranch')
        sha = local_repo.refs.read_loose_ref('refs/heads/mybranch')
        swift.SwiftRepo.init_bare(self.scon)
        tcp_client = client.TCPGitClient(self.server_address,
                                         port=self.port)
        tcp_client.send_pack("/fakerepo",
                             determine_wants,
                             local_repo.object_store.generate_pack_contents)
        swift_repo = swift.SwiftRepo(self.fakerepo)
        remote_sha = swift_repo.refs.read_loose_ref('refs/heads/mybranch')
        self.assertEqual(sha, remote_sha)

    def test_push_multiple_branch(self):
        def determine_wants(*args):
            return {"refs/heads/mybranch":
                    local_repo.refs["refs/heads/mybranch"],
                    "refs/heads/master":
                    local_repo.refs["refs/heads/master"],
                    "refs/heads/pullr-108":
                    local_repo.refs["refs/heads/pullr-108"]}

        local_repo = repo.Repo.init(self.temp_d, mkdir=True)
        # Nothing in the staging area
        local_shas = {}
        remote_shas = {}
        for branch in ('master', 'mybranch', 'pullr-108'):
            local_shas[branch] = local_repo.do_commit(
                'Test commit %s' % branch, 'fbo@localhost',
                ref='refs/heads/%s' % branch)
        swift.SwiftRepo.init_bare(self.scon)
        tcp_client = client.TCPGitClient(self.server_address,
                                         port=self.port)
        tcp_client.send_pack(self.fakerepo,
                             determine_wants,
                             local_repo.object_store.generate_pack_contents)
        swift_repo = swift.SwiftRepo("fakerepo")
        for branch in ('master', 'mybranch', 'pullr-108'):
            remote_shas[branch] = swift_repo.refs.read_loose_ref(
                'refs/heads/%s' % branch)
        self.assertDictEqual(local_shas, remote_shas)

    def test_push_data_branch(self):
        def determine_wants(*args):
            return {"refs/heads/master": local_repo.refs["HEAD"]}
        local_repo = repo.Repo.init(self.temp_d, mkdir=True)
        os.mkdir(os.path.join(self.temp_d, "dir"))
        files = ('testfile', 'testfile2', 'dir/testfile3')
        i = 0
        for f in files:
            file(os.path.join(self.temp_d, f), 'w').write("DATA %s" % i)
            i += 1
        local_repo.stage(files)
        local_repo.do_commit('Test commit', 'fbo@localhost',
                             ref='refs/heads/master')
        swift.SwiftRepo.init_bare(self.scon)
        tcp_client = client.TCPGitClient(self.server_address,
                                         port=self.port)
        tcp_client.send_pack(self.fakerepo,
                             determine_wants,
                             local_repo.object_store.generate_pack_contents)
        swift_repo = swift.SwiftRepo("fakerepo")
        commit_sha = swift_repo.refs.read_loose_ref('refs/heads/master')
        otype, data = swift_repo.object_store.get_raw(commit_sha)
        commit = objects.ShaFile.from_raw_string(otype, data)
        otype, data = swift_repo.object_store.get_raw(commit._tree)
        tree = objects.ShaFile.from_raw_string(otype, data)
        objs = tree.items()
        objs_ = []
        for tree_entry in objs:
            objs_.append(swift_repo.object_store.get_raw(tree_entry.sha))
        # Blob
        self.assertEqual(objs_[1][1], 'DATA 0')
        self.assertEqual(objs_[2][1], 'DATA 1')
        # Tree
        self.assertEqual(objs_[0][0], 2)

    def test_clone_then_push_data(self):
        self.test_push_data_branch()
        shutil.rmtree(self.temp_d)
        local_repo = repo.Repo.init(self.temp_d, mkdir=True)
        tcp_client = client.TCPGitClient(self.server_address,
                                         port=self.port)
        remote_refs = tcp_client.fetch(self.fakerepo, local_repo)
        files = (os.path.join(self.temp_d, 'testfile'),
                 os.path.join(self.temp_d, 'testfile2'))
        local_repo["HEAD"] = remote_refs["refs/heads/master"]
        indexfile = local_repo.index_path()
        tree = local_repo["HEAD"].tree
        index.build_index_from_tree(local_repo.path, indexfile,
                                    local_repo.object_store, tree)
        for f in files:
            self.assertEqual(os.path.isfile(f), True)

        def determine_wants(*args):
            return {"refs/heads/master": local_repo.refs["HEAD"]}
        os.mkdir(os.path.join(self.temp_d, "test"))
        files = ('testfile11', 'testfile22', 'test/testfile33')
        i = 0
        for f in files:
            file(os.path.join(self.temp_d, f), 'w').write("DATA %s" % i)
            i += 1
        local_repo.stage(files)
        local_repo.do_commit('Test commit', 'fbo@localhost',
                             ref='refs/heads/master')
        tcp_client.send_pack("/fakerepo",
                             determine_wants,
                             local_repo.object_store.generate_pack_contents)

    def test_push_remove_branch(self):
        def determine_wants(*args):
            return {"refs/heads/pullr-108": objects.ZERO_SHA,
                    "refs/heads/master":
                    local_repo.refs['refs/heads/master'],
                    "refs/heads/mybranch":
                    local_repo.refs['refs/heads/mybranch'],
                    }
        self.test_push_multiple_branch()
        local_repo = repo.Repo(self.temp_d)
        tcp_client = client.TCPGitClient(self.server_address,
                                         port=self.port)
        tcp_client.send_pack(self.fakerepo,
                             determine_wants,
                             local_repo.object_store.generate_pack_contents)
        swift_repo = swift.SwiftRepo("fakerepo")
        self.assertNotIn('refs/heads/pullr-108', swift_repo.refs.allkeys())

    def test_push_annotated_tag(self):
        def determine_wants(*args):
            return {"refs/heads/master": local_repo.refs["HEAD"],
                    "refs/tags/v1.0": local_repo.refs["refs/tags/v1.0"]}
        local_repo = repo.Repo.init(self.temp_d, mkdir=True)
        # Nothing in the staging area
        sha = local_repo.do_commit('Test commit', 'fbo@localhost')
        otype, data = local_repo.object_store.get_raw(sha)
        commit = objects.ShaFile.from_raw_string(otype, data)
        tag = objects.Tag()
        tag.tagger = "fbo@localhost"
        tag.message = "Annotated tag"
        tag.tag_timezone = objects.parse_timezone('-0200')[0]
        tag.tag_time = commit.author_time
        tag.object = (objects.Commit, commit.id)
        tag.name = "v0.1"
        local_repo.object_store.add_object(tag)
        local_repo.refs['refs/tags/v1.0'] = tag.id
        swift.SwiftRepo.init_bare(self.scon)
        tcp_client = client.TCPGitClient(self.server_address,
                                         port=self.port)
        tcp_client.send_pack(self.fakerepo,
                             determine_wants,
                             local_repo.object_store.generate_pack_contents)
        swift_repo = swift.SwiftRepo(self.fakerepo)
        tag_sha = swift_repo.refs.read_loose_ref('refs/tags/v1.0')
        otype, data = swift_repo.object_store.get_raw(tag_sha)
        rtag = objects.ShaFile.from_raw_string(otype, data)
        self.assertEqual(rtag.object[1], commit.id)
        self.assertEqual(rtag.id, tag.id)


if __name__ == '__main__':
    unittest.main()