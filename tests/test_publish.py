"""Tests for Publish in tools/cms.py: GET/POST /api/publish and the background job.

REAL git, never the network and never github.com: every test gets a copy of a fixture site that is a git
repository with a local identity, and a local BARE repository as `origin`. The publish gate runs the
fixture's own tiny tests/ folder, so these tests can inject a failing one.
"""
import ast
import json
import os
import re
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import cms_fixture as F
from cms_fixture import git
from support import ROOT

CMS_SOURCE = (ROOT / "tools" / "cms.py").read_text(encoding="utf-8")
CMS = F.load_cms(ROOT)
SLOW = 300


class PublishCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = F.GitSite()
        cls.work = Path(tempfile.mkdtemp(prefix="publish-test-"))
        cls.tmp, cls.bare = cls.work / "site", cls.work / "origin.git"
        cls.template.copy_to(cls.tmp, cls.bare)
        cls.cms = F.load_cms(cls.tmp)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), cls.cms.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        shutil.rmtree(str(cls.work), ignore_errors=True)
        cls.template.close()

    def setUp(self):
        self.template.copy_to(self.tmp, self.bare)
        self.cms.BUILD.update(last_run="", ok=None, ready=None, blockers=None, items=[], log="", version="")
        self.cms.JOB.clear()
        self.cms.JOB.update(self.cms.IDLE_JOB)
        self.cms._KNOWN_SECRETS.clear()

    # -- helpers ------------------------------------------------------------------------------
    def call(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def version(self):
        return self.call("GET", "/api/state")[1]["version"]

    def status(self):
        st, d = self.call("GET", "/api/publish")
        self.assertEqual(st, 200)
        return d

    def start(self, message=None, version=None):
        body = {"version": self.version() if version is None else version}
        if message is not None:
            body["message"] = message
        return self.call("POST", "/api/publish", body)

    def job(self):
        return self.call("GET", "/api/publish/job")[1]

    def finish(self, timeout=SLOW):
        end = time.time() + timeout
        while time.time() < end:
            j = self.job()
            if j["state"] != "running":
                return j
            time.sleep(0.1)
        self.fail("the publish job did not finish: %r" % self.job())

    def publish(self, message=None):
        st, d = self.start(message)
        self.assertEqual(st, 200, d)
        self.assertTrue(d["job"])
        return self.finish()

    def hide_beta(self):
        """A real edit through the manager: rebuilds, so the working tree has uncommitted changes."""
        st, d = self.call("POST", "/api/projects/beta/visibility", {"hidden": True, "version": self.version()})
        self.assertEqual(st, 200, d)

    def origin_head(self):
        return git(self.bare, "rev-parse", "main")

    def origin_count(self):
        return int(git(self.bare, "rev-list", "--count", "main"))

    def head(self):
        return git(self.tmp, "rev-parse", "HEAD")

    def local_count(self):
        return int(git(self.tmp, "rev-list", "--count", "HEAD"))

    def write(self, rel, text="x\n"):
        f = self.tmp / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
        return f

    def push_from_elsewhere(self, name="other.txt"):
        """GitHub gets a commit this copy has not seen (made in a second clone of the bare origin)."""
        other = self.work / "other-clone"
        shutil.rmtree(str(other), ignore_errors=True)
        git(self.work, "clone", "-q", str(self.bare), str(other))
        git(other, "config", "user.name", "Someone Else")
        git(other, "config", "user.email", "else@example.invalid")
        (other / name).write_text("from another machine\n", encoding="utf-8")
        git(other, "add", "-A")
        git(other, "commit", "-q", "-m", "Newer commit on GitHub")
        git(other, "push", "-q", "origin", "HEAD:main")
        return git(other, "rev-parse", "HEAD")

    def assert_nothing_published(self, head_before, origin_before):
        self.assertEqual(self.head(), head_before, "a refused publish must not commit")
        self.assertEqual(self.origin_head(), origin_before, "a refused publish must not push")


class TestStatus(PublishCase):
    def test_a_fresh_copy_is_all_published(self):
        d = self.status()
        self.assertEqual(sorted(d), sorted(["ok", "remote", "remote_url", "branch", "ahead", "behind", "dirty",
                                            "changed_files", "release_ready", "last_commit", "live_url",
                                            "reachable", "running", "can_publish", "reason"]))
        self.assertEqual((d["remote"], d["remote_url"], d["branch"]), (True, str(self.bare), "main"))
        self.assertEqual((d["ahead"], d["behind"], d["dirty"], d["release_ready"]), (0, 0, False, True))
        self.assertEqual(d["last_commit"], git(self.tmp, "log", "-1", "--format=%h %s"))
        self.assertTrue(d["last_commit"].endswith("Fixture site"))
        self.assertEqual(d["live_url"], "https://example.com/")
        self.assertEqual((d["can_publish"], d["reason"]), (True, ""))

    def test_no_remote_means_no_publishing(self):
        git(self.tmp, "remote", "remove", "origin")
        d = self.status()
        self.assertEqual((d["remote"], d["remote_url"], d["can_publish"]), (False, "", False))
        self.assertIn("remote", d["reason"].lower())
        head = self.head()
        st, job = self.start()
        self.assertEqual(st, 200)
        j = self.finish()
        self.assertEqual(j["state"], "failed")
        self.assertIn("remote", j["error"].lower())
        self.assertEqual(self.head(), head)

    def test_a_folder_that_is_not_a_git_repo_cannot_publish(self):
        plain = Path(tempfile.mkdtemp(prefix="publish-plain-"))
        try:
            F.make_site(plain)
            cms = F.load_cms(plain)
            d = cms.publish_status()
        finally:
            shutil.rmtree(str(plain), ignore_errors=True)
        self.assertEqual((d["remote"], d["can_publish"]), (False, False))
        self.assertTrue(d["reason"])

    def test_edits_make_it_dirty_and_untracked_files_count_but_ignored_ones_do_not(self):
        self.hide_beta()
        d = self.status()
        self.assertEqual((d["dirty"], d["ahead"], d["can_publish"]), (True, 0, True))
        self.assertGreater(d["changed_files"], 0)
        git(self.tmp, "checkout", "-q", "--", ".")
        git(self.tmp, "clean", "-fdq")
        self.assertFalse(self.status()["dirty"])
        self.write("staging/notes.txt")                      # .gitignore keeps staging/ out
        (self.tmp / "tools" / "backups").mkdir(parents=True, exist_ok=True)
        self.write("tools/backups/projects-1.json")
        self.assertFalse(self.status()["dirty"])
        self.write("assets/new-photo.txt")                   # a new, non-ignored file is a change
        self.assertTrue(self.status()["dirty"])

    def test_a_local_commit_that_is_not_pushed_is_ahead(self):
        self.write("note.txt")
        git(self.tmp, "add", "-A")
        git(self.tmp, "commit", "-q", "-m", "Local only")
        d = self.status()
        self.assertEqual((d["ahead"], d["behind"], d["dirty"], d["can_publish"]), (1, 0, False, True))

    def test_github_having_newer_commits_blocks_publishing_and_the_status_fetches_for_itself(self):
        self.push_from_elsewhere()
        d = self.status()                                    # no fetch was run by the test
        self.assertEqual((d["behind"], d["ahead"], d["can_publish"]), (1, 0, False))
        self.assertIn("newer commits", d["reason"])
        self.assertIn("Hermes", d["reason"])

    def test_another_branch_cannot_publish(self):
        git(self.tmp, "checkout", "-q", "-b", "scratch")
        d = self.status()
        self.assertEqual((d["branch"], d["can_publish"]), ("scratch", False))
        self.assertIn("scratch", d["reason"])
        head, origin = self.head(), self.origin_head()
        self.start()
        self.assertEqual(self.finish()["state"], "failed")
        self.assert_nothing_published(head, origin)

    def test_a_site_that_is_not_release_ready_says_so_in_the_status(self):
        st, d = self.call("POST", "/api/config", {"showreel": {"hidden": False}, "version": self.version()})
        self.assertEqual(st, 200, d)
        self.assertFalse(self.status()["release_ready"])

    def test_an_unreachable_github_is_reported_not_raised(self):
        git(self.tmp, "remote", "set-url", "origin", str(self.work / "no-such-origin.git"))
        d = self.status()
        self.assertEqual((d["reachable"], d["can_publish"], d["reason"]), (False, False, "Cannot reach GitHub"))

    def test_the_status_is_served_only_on_this_machine(self):
        req = urllib.request.Request("http://127.0.0.1:%d/api/publish" % self.port, headers={"Host": "evil.example"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=20)
        self.assertEqual(ctx.exception.code, 403)
        req = urllib.request.Request("http://127.0.0.1:%d/api/publish" % self.port, data=b"{}", method="POST",
                                     headers={"Origin": "https://evil.example"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=20)
        self.assertEqual(ctx.exception.code, 403)


class TestRefusals(PublishCase):
    """Every one of these must leave the local history and GitHub exactly as they were."""

    def test_behind_is_refused_and_nothing_is_committed(self):
        self.hide_beta()
        self.push_from_elsewhere()
        head, origin = self.head(), self.origin_head()
        j = self.publish()
        self.assertEqual(j["state"], "failed")
        self.assertIn("GitHub has newer commits; ask Hermes to merge them first", j["error"])
        self.assert_nothing_published(head, origin)
        self.assertTrue(self.status()["dirty"], "the owner's edits are still there")
        self.assertEqual(git(self.tmp, "stash", "list"), "", "never stashes")

    def test_a_site_that_is_not_release_ready_is_refused_with_the_reason(self):
        st, d = self.call("POST", "/api/config", {"showreel": {"hidden": False}, "version": self.version()})
        self.assertEqual(st, 200, d)
        head, origin = self.head(), self.origin_head()
        j = self.publish()
        self.assertEqual(j["state"], "failed")
        self.assertIn("not release-ready", j["error"])
        self.assertIn("RELEASE BLOCKED", j["error"])
        self.assertIn("showreel", j["error"])
        self.assert_nothing_published(head, origin)

    def test_a_failing_test_refuses_the_publish_and_names_it(self):
        self.hide_beta()
        self.write("tests/test_broken.py",
                   "import unittest\n\n\nclass TestBroken(unittest.TestCase):\n"
                   "    def test_this_one_fails(self):\n        self.assertEqual(1, 2)\n")
        head, origin = self.head(), self.origin_head()
        j = self.publish()
        self.assertEqual(j["state"], "failed")
        self.assertIn("tests failed", j["error"])
        self.assertIn("test_this_one_fails", j["error"])
        self.assertIn("test_broken.TestBroken", j["error"])
        self.assert_nothing_published(head, origin)
        self.assertEqual(j["step"], "Running the tests")

    def test_a_test_that_cannot_even_be_imported_also_refuses(self):
        self.write("tests/test_syntax.py", "def broken(:\n")
        head, origin = self.head(), self.origin_head()
        self.assertEqual(self.publish()["state"], "failed")
        self.assert_nothing_published(head, origin)

    def test_a_secret_looking_file_is_refused_and_nothing_is_committed_or_left_staged(self):
        self.hide_beta()
        self.write(".env", "TOKEN=abc\n")
        self.write("assets/deploy.pem", "-----BEGIN-----\n")
        head, origin = self.head(), self.origin_head()
        j = self.publish()
        self.assertEqual(j["state"], "failed")
        self.assertIn(".env", j["error"])
        self.assertIn("deploy.pem", j["error"])
        self.assert_nothing_published(head, origin)
        self.assertEqual(git(self.tmp, "diff", "--cached", "--name-only"), "", "the refusal leaves the index alone")

    def test_every_secret_shape_in_the_brief_is_recognised(self):
        for name in (".env", ".env.local", "config/.env.production", ".claude/settings.local.json", "api-token.txt",
                     "my_Token.json", "client_secret.json", "credentials.json", "AWS-Credential.txt", "server.pem",
                     "deploy.key", "id_rsa", "id_rsa.pub", "keys/id_rsa_work", "secrets/notes.txt"):
            self.assertTrue(any(self.cms.SECRET_PATH.match(p) for p in name.split("/")), name)
        for name in ("index.html", "assets/img/alpha/01.jpg", "environment.md", "tools/cms.py", "keynote.pdf",
                     "monkey.png", "README.md"):
            self.assertFalse(any(self.cms.SECRET_PATH.match(p) for p in name.split("/")), name)

    def test_a_secret_looking_name_is_caught_even_when_it_is_the_only_change(self):
        self.write("settings.local.json", "{}\n")
        head, origin = self.head(), self.origin_head()
        j = self.publish()
        self.assertEqual(j["state"], "failed")
        self.assertIn("settings.local.json", j["error"])
        self.assert_nothing_published(head, origin)

    def test_a_blob_of_fifty_megabytes_is_refused(self):
        big = self.tmp / "assets" / "huge.bin"
        big.parent.mkdir(parents=True, exist_ok=True)
        with open(str(big), "wb") as f:
            f.truncate(50 * 1024 * 1024)                    # exactly the limit, sparse
        head, origin = self.head(), self.origin_head()
        j = self.publish()
        self.assertEqual(j["state"], "failed")
        self.assertIn("huge.bin", j["error"])
        self.assertIn("50 MB", j["error"])
        self.assert_nothing_published(head, origin)
        self.assertEqual(git(self.tmp, "diff", "--cached", "--name-only"), "")

    def test_a_blob_just_under_the_limit_is_fine(self):
        big = self.tmp / "assets" / "almost.bin"
        big.parent.mkdir(parents=True, exist_ok=True)
        with open(str(big), "wb") as f:
            f.truncate(50 * 1024 * 1024 - 1)
        j = self.publish()
        self.assertEqual(j["state"], "done", j)
        self.assertEqual(self.origin_count(), 2)

    def test_no_git_identity_fails_clearly_and_invents_none(self):
        git(self.tmp, "config", "--unset", "user.name")
        git(self.tmp, "config", "--unset", "user.email")
        self.hide_beta()
        head, origin = self.head(), self.origin_head()
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}):
            j = self.publish()
        self.assertEqual(j["state"], "failed")
        self.assertIn("user.name", j["error"])
        self.assert_nothing_published(head, origin)

    def test_a_merge_in_progress_is_refused(self):
        (Path(git(self.tmp, "rev-parse", "--absolute-git-dir")) / "MERGE_HEAD").write_text(self.head() + "\n")
        d = self.status()
        self.assertFalse(d["can_publish"])
        self.assertIn("merge", d["reason"])

    def test_a_stale_page_is_refused_and_starts_no_job(self):
        st, d = self.start(version="not-the-version")
        self.assertEqual((st, d.get("stale")), (409, True))
        self.assertEqual(self.job()["state"], "idle")

    def test_a_bad_message_is_a_400(self):
        st, d = self.start(message=["not", "text"])
        self.assertEqual(st, 400)
        st, d = self.start(message="x" * 600)
        self.assertEqual(st, 400)
        self.assertEqual(self.job()["state"], "idle")


class TestPublishing(PublishCase):
    def test_the_happy_path_makes_exactly_one_commit_on_origin_and_leaves_the_tree_clean(self):
        self.hide_beta()
        self.write("staging/notes.txt")                      # ignored, must never be committed
        before = self.origin_count()
        self.assertEqual(self.status()["can_publish"], True)
        j = self.publish("Hide Beta from the site")
        self.assertEqual((j["state"], j["error"]), ("done", ""), j["log"])
        self.assertEqual(self.origin_count(), before + 1)
        self.assertEqual(self.origin_head(), self.head())
        self.assertEqual(git(self.bare, "log", "-1", "--format=%s", "main"), "Hide Beta from the site")
        self.assertEqual(git(self.tmp, "status", "--porcelain"), "")
        self.assertEqual(j["commit"], git(self.tmp, "rev-parse", "--short", "HEAD"))
        self.assertEqual(j["step"], "Done")
        changed = git(self.bare, "show", "--name-only", "--format=", "main").splitlines()
        self.assertIn("projects.json", changed)
        self.assertIn("work/beta.html", changed, "hiding a project removes its page")
        self.assertFalse([c for c in changed if c.startswith("staging/")])
        d = self.status()
        self.assertEqual((d["ahead"], d["behind"], d["dirty"], d["can_publish"]), (0, 0, False, True))
        self.assertIn("Tests: passed", j["log"])

    def test_the_default_message_is_used_when_none_is_given(self):
        self.hide_beta()
        j = self.publish()
        self.assertEqual(j["state"], "done", j)
        self.assertEqual(git(self.bare, "log", "-1", "--format=%s", "main"), "Site update (edited in the site manager)")
        self.hide_beta()                                      # already hidden: nothing changes
        self.assertEqual(self.status()["dirty"], False)

    def test_the_job_reports_each_step_in_order(self):
        seen = []
        real = self.cms._step

        def spy(i):
            seen.append(self.cms.PUBLISH_STEPS[i])
            real(i)
        self.hide_beta()
        with mock.patch.object(self.cms, "_step", spy):
            j = self.publish()
        self.assertEqual(j["state"], "done")
        self.assertEqual(seen, list(self.cms.PUBLISH_STEPS))
        self.assertEqual(j["steps"], list(self.cms.PUBLISH_STEPS))
        for name in self.cms.PUBLISH_STEPS:
            self.assertIn("== " + name, j["log"])

    def test_a_github_remote_gives_a_commit_link(self):
        self.hide_beta()
        url = "https://tok:Sup3rS3cretPw@github.com/amrf-cc/riveraferran.com.git"
        with mock.patch.object(self.cms, "_origin_url", return_value=url):
            j = self.publish()
            shown = json.dumps([self.status(), self.job()])
        self.assertEqual(j["state"], "done", j)
        self.assertEqual(j["url"], "https://github.com/amrf-cc/riveraferran.com/commit/" + j["commit"])
        self.assertNotIn("Sup3rS3cretPw", shown)

    def test_nothing_to_publish(self):
        before = (self.head(), self.origin_head())
        j = self.publish()
        self.assertEqual(j["state"], "done")
        self.assertEqual(j["message"], "Nothing to publish")
        self.assertEqual((j["commit"], j["url"]), ("", ""))
        self.assertEqual((self.head(), self.origin_head()), before)
        self.assertEqual(git(self.tmp, "status", "--porcelain"), "")

    def test_commits_that_are_already_ahead_are_pushed_even_with_nothing_new_to_commit(self):
        self.write("note.txt")
        git(self.tmp, "add", "-A")
        git(self.tmp, "commit", "-q", "-m", "Committed by hand")
        local = self.head()
        self.assertEqual(self.status()["ahead"], 1)
        j = self.publish()
        self.assertEqual(j["state"], "done", j)
        self.assertEqual(self.origin_head(), local, "pushed the existing commit, made no new one")
        self.assertEqual(j["commit"], git(self.tmp, "rev-parse", "--short", "HEAD"))
        self.assertEqual(self.local_count(), 2)
        self.assertEqual(self.status()["ahead"], 0)

    def test_an_ahead_commit_and_new_edits_publish_as_two_commits(self):
        self.write("note.txt")
        git(self.tmp, "add", "-A")
        git(self.tmp, "commit", "-q", "-m", "Committed by hand")
        self.hide_beta()
        j = self.publish("Then the edit")
        self.assertEqual(j["state"], "done", j)
        self.assertEqual(self.origin_count(), 3)
        self.assertEqual(self.origin_head(), self.head())

    def test_a_push_rejected_by_origin_fails_the_job_and_keeps_the_local_commit(self):
        hook = self.bare / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho 'pre-receive: not today' >&2\nexit 1\n")
        hook.chmod(0o755)
        self.hide_beta()
        origin = self.origin_head()
        before = self.local_count()
        j = self.publish("Will be rejected")
        self.assertEqual(j["state"], "failed")
        self.assertIn("did not accept", j["error"])
        self.assertIn("not today", j["log"])
        self.assertEqual(self.origin_head(), origin)
        self.assertEqual(self.local_count(), before + 1, "the commit is kept locally")
        self.assertEqual(git(self.tmp, "log", "-1", "--format=%s"), "Will be rejected")
        d = self.status()
        self.assertEqual((d["ahead"], d["dirty"]), (1, False))
        hook.unlink()                                        # GitHub accepts again: the next publish pushes it
        j = self.publish()
        self.assertEqual(j["state"], "done")
        self.assertEqual(self.origin_head(), self.head())
        self.assertEqual(self.local_count(), before + 1, "no second commit was made")

    def test_two_posts_at_once_start_one_job_and_the_other_gets_409(self):
        gate, started = threading.Event(), threading.Event()

        def slow_tests():
            started.set()
            gate.wait(60)
            return True, [], "ok"
        self.hide_beta()
        version = self.version()
        results = []

        def post():
            results.append(self.call("POST", "/api/publish", {"version": version}))
        with mock.patch.object(self.cms, "_run_tests", slow_tests):
            threads = [threading.Thread(target=post) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(60)
            try:
                self.assertEqual(sorted(r[0] for r in results), [200, 409], results)
                self.assertTrue(started.wait(60))
                self.assertEqual(self.job()["state"], "running")
                d = self.status()                             # the page keeps polling while a job runs
                self.assertEqual((d["running"], d["can_publish"]), (True, False))
                self.assertIn("already running", d["reason"])
                self.assertEqual(self.start(version=version)[0], 409)
            finally:
                gate.set()
            j = self.finish()
        self.assertEqual(j["state"], "done", j)
        self.assertEqual(self.origin_count(), 2, "one job, one commit")

    def test_a_job_that_crashes_ends_failed_not_running(self):
        with mock.patch.object(self.cms, "_run_tests", side_effect=RuntimeError("boom")):
            j = self.publish()
        self.assertEqual(j["state"], "failed")
        self.assertIn("boom", j["error"])
        self.assertEqual(self.start()[0], 200, "and the slot is free again")
        self.finish()

    def test_a_save_waits_for_the_publish_lock_and_cannot_interleave(self):
        """Steps b-f hold the data lock: a save started meanwhile only runs after the commit."""
        gate, started = threading.Event(), threading.Event()

        def slow_tests():
            started.set()
            gate.wait(60)
            return True, [], "ok"
        self.hide_beta()
        out = {}
        with mock.patch.object(self.cms, "_run_tests", slow_tests):
            self.assertEqual(self.start()[0], 200)
            self.assertTrue(started.wait(60))
            v = self.cms.data_version()                       # /api/state would wait for the lock
            t = threading.Thread(target=lambda: out.update(r=self.call(
                "POST", "/api/projects/gamma/visibility", {"hidden": True, "version": v})))
            t.start()
            time.sleep(1.0)
            self.assertNotIn("r", out, "the save is waiting for the publish to finish its commit")
            gate.set()
            t.join(120)
            self.finish()
        self.assertEqual(out["r"][0], 200)


class TestCredentials(PublishCase):
    SECRET = "Sup3rS3cretPw99"

    def test_credentials_in_the_remote_url_never_reach_the_status_or_the_job_log(self):
        # 127.0.0.1:9 is this machine's closed discard port: the fetch fails at once and never leaves it.
        git(self.tmp, "remote", "set-url", "origin", "https://deploy:%s@127.0.0.1:9/amrf-cc/site.git" % self.SECRET)
        with mock.patch.dict(os.environ, {"NO_PROXY": "*", "no_proxy": "*", "HTTPS_PROXY": "", "https_proxy": ""}):
            d = self.status()
            self.assertEqual((d["remote"], d["reachable"], d["can_publish"]), (True, False, False))
            self.assertEqual(d["reason"], "Cannot reach GitHub")
            self.assertEqual(d["remote_url"], "https://127.0.0.1:9/amrf-cc/site.git")
            self.hide_beta()
            j = self.publish()
            everything = json.dumps([d, j, self.status(), self.job()])
        self.assertEqual(j["state"], "failed")
        self.assertIn("Cannot reach GitHub", j["error"])
        self.assertNotIn(self.SECRET, everything)
        self.assertNotIn("deploy:", everything)

    def test_scrub_removes_anything_that_looks_like_a_credential(self):
        s = self.cms.scrub
        for raw in ("fatal: unable to access 'https://user:hunter2hunter2@github.com/o/r.git/': 403",
                    "remote: ghp_abcdefghijklmnopqrstuvwxyz0123456789 rejected",
                    "github_pat_11ABCDEFG0abcdefghijklmnop_qrstuvwxyz",
                    "Authorization: Bearer abcdef0123456789abcdef",
                    "password=hunter2hunter2", "token: abcdef0123456789", "api_key = abcdef0123456789",
                    "glpat-abcdefghijklmnopqrstu", "AKIAABCDEFGHIJKLMNOP",
                    "ssh://git:hunter2hunter2@example.com/o/r.git"):
            out = s(raw)
            for needle in ("hunter2hunter2", "abcdefghijklmnop", "abcdef0123456789", "AKIAABCDEFGHIJKLMNOP",
                           "qrstuvwxyz"):
                self.assertNotIn(needle, out, raw)
        # ordinary text and commit ids survive
        plain = "Committed 3 files at 3faf8ae12cd04e5c5f4d2f0c9d7a1b1f2e3d4c5b (main -> main)"
        self.assertEqual(s(plain), plain)

    def test_the_remote_url_shown_has_no_credentials(self):
        f = self.cms.strip_credentials
        self.assertEqual(f("https://tok:pw@github.com/o/r.git"), "https://github.com/o/r.git")
        self.assertEqual(f("https://ghp_abc@github.com/o/r.git"), "https://github.com/o/r.git")
        self.assertEqual(f("ssh://git:pw@github.com/o/r.git"), "ssh://git@github.com/o/r.git")
        self.assertEqual(f("git@github.com:o/r.git"), "git@github.com:o/r.git")
        self.assertEqual(f("/tmp/origin.git"), "/tmp/origin.git")

    def test_the_commit_link_is_derived_from_the_github_remote(self):
        f = self.cms.github_commit_url
        want = "https://github.com/amrf-cc/riveraferran.com/commit/abc1234"
        for url in ("https://github.com/amrf-cc/riveraferran.com.git", "https://github.com/amrf-cc/riveraferran.com",
                    "https://tok:pw@github.com/amrf-cc/riveraferran.com.git", "git@github.com:amrf-cc/riveraferran.com.git",
                    "ssh://git@github.com/amrf-cc/riveraferran.com.git", "https://github.com/amrf-cc/riveraferran.com/"):
            self.assertEqual(f(url, "abc1234"), want, url)
        self.assertEqual(f("/some/local/origin.git", "abc1234"), "")
        self.assertEqual(f("https://gitlab.com/o/r.git", "abc1234"), "")


class TestSource(unittest.TestCase):
    """The push command is the dangerous one: pin its exact shape in the source."""

    @staticmethod
    def publish_region():
        a = CMS_SOURCE.index("# --------------------------------------------------------------------------- publish")
        b = CMS_SOURCE.index("# --------------------------------------------------------------------------- state")
        return CMS_SOURCE[a:b]

    def test_the_only_push_is_origin_head_main(self):
        region = self.publish_region()
        tree = ast.parse(CMS_SOURCE)
        pushes = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                consts = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
                if "push" in consts:
                    pushes.append((consts, [k.arg for k in node.keywords]))
        self.assertEqual(len(pushes), 1, "exactly one git push call")
        self.assertEqual(pushes[0][0], ["push", "origin", "HEAD:main"])
        self.assertEqual(pushes[0][1], ["timeout"])
        for forbidden in ("--force", "--force-with-lease", "--delete", "--mirror", "--prune", "--all", "--tags",
                          "+HEAD", "+refs", "reset", "stash", "rebase", "merge", "clean"):
            code = re.sub(r"#.*", "", re.sub(r'"""[\s\S]*?"""', "", region))
            for m in re.finditer(r'"([^"\n]*)"', code):
                self.assertNotEqual(m.group(1), forbidden, forbidden)
        self.assertNotRegex(re.sub(r'"""[\s\S]*?"""', "", region), r'"-f"|"-[a-z]*f[a-z]*"')
        self.assertEqual(re.findall(r'"HEAD:[^"]*"', region), ['"HEAD:main"'], "no refspec but HEAD:main")
        self.assertNotRegex(region, r"://[^/\s\"']*:[^/\s\"']*@")          # no URL with credentials in the code

    def test_git_runs_never_prompt(self):
        self.assertIn('GIT_TERMINAL_PROMPT"] = "0"', CMS_SOURCE)
        self.assertNotIn("shell=True", self.publish_region())

    def test_the_gate_skips_exactly_the_slow_and_self_referential_modules(self):
        self.assertEqual(CMS.PUBLISH_GATE_SKIP, ("test_cms_ui", "test_legacy_redirects", "test_publish"))
        for module in CMS.PUBLISH_GATE_SKIP:
            self.assertTrue((ROOT / "tests" / (module + ".py")).exists(), module)


if __name__ == "__main__":
    unittest.main()
