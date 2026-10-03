import os
import tempfile
import unittest

import rotation


class RotationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.ssd = os.path.join(root, "ssd")
        for season, count in (("show_s1", 3), ("show_s2", 2), ("show_s10", 1)):
            os.makedirs(os.path.join(self.ssd, season))
            for ep in range(1, count + 1):
                open(os.path.join(self.ssd, season, f"Show {season[-2:]}E{ep} Title.mp4"), "w").close()
        open(os.path.join(self.ssd, "show_s1", ".hidden.mp4"), "w").close()
        open(os.path.join(self.ssd, "show_s1", "notes.txt"), "w").close()
        os.makedirs(os.path.join(self.ssd, "$RECYCLE.BIN"))
        self.show = rotation.Show(
            "show", os.path.join(self.ssd, "show_s*"),
            os.path.join(root, "rotation", "show"), os.path.join(root, "state"))

    def tearDown(self):
        self.tmp.cleanup()

    def names(self, paths):
        return [os.path.basename(p) for p in paths]

    def links(self):
        out = self.show.output_dir
        return [(n, os.path.basename(os.readlink(os.path.join(out, n)))) for n in sorted(os.listdir(out))]

    def test_episode_order_is_natural_and_filtered(self):
        self.assertEqual(self.names(self.show.episodes()), [
            "Show s1E1 Title.mp4", "Show s1E2 Title.mp4", "Show s1E3 Title.mp4",
            "Show s2E1 Title.mp4", "Show s2E2 Title.mp4", "Show 10E1 Title.mp4"])

    def test_first_rebuild_starts_at_beginning(self):
        rotation.rebuild([self.show], duration_fn=lambda p: 1500)
        links = self.links()
        self.assertEqual(len(links), 6)
        self.assertEqual(links[0], ("0001_Show_s1E1_Title.mp4", "Show s1E1 Title.mp4"))
        self.assertTrue(all(os.path.exists(os.path.join(self.show.output_dir, n)) for n, _ in links))

    def test_interrupted_episode_is_replayed(self):
        eps = self.show.episodes()
        self.show.save_state({"current": eps[3], "started": 1000, "last_seen": 1000 + 600})
        rotation.rebuild([self.show], duration_fn=lambda p: 1500)
        links = self.links()
        self.assertEqual(links[0][1], "Show s2E1 Title.mp4")
        self.assertEqual(links[-1][1], "Show s1E3 Title.mp4")

    def test_finished_episode_moves_on(self):
        eps = self.show.episodes()
        self.show.save_state({"current": eps[3], "started": 1000, "last_seen": 1000 + 1440})
        rotation.rebuild([self.show], duration_fn=lambda p: 1500)
        self.assertEqual(self.links()[0][1], "Show s2E2 Title.mp4")

    def test_wraps_after_last_episode(self):
        eps = self.show.episodes()
        self.show.save_state({"current": eps[-1], "started": 0, "last_seen": 2000})
        rotation.rebuild([self.show], duration_fn=lambda p: 1500)
        self.assertEqual(self.links()[0][1], "Show s1E1 Title.mp4")

    def test_removed_episode_resumes_at_next_in_order(self):
        eps = self.show.episodes()
        gone = eps[1]
        os.remove(gone)
        self.show.save_state({"current": gone, "started": 0, "last_seen": 10})
        rotation.rebuild([self.show], duration_fn=lambda p: 1500)
        self.assertEqual(self.links()[0][1], "Show s1E3 Title.mp4")

    def test_missing_ssd_leaves_folder_untouched(self):
        rotation.rebuild([self.show], duration_fn=lambda p: 1500)
        before = self.links()
        self.show.source_glob = os.path.join(self.tmp.name, "unplugged", "show_s*")
        self.assertEqual(rotation.rebuild([self.show], duration_fn=lambda p: 1500), 0)
        self.assertEqual(self.links(), before)

    def test_rebuild_replaces_stale_and_stray_entries(self):
        rotation.rebuild([self.show], duration_fn=lambda p: 1500)
        os.symlink(self.show.output_dir, os.path.join(self.show.output_dir, "show"))
        eps = self.show.episodes()
        self.show.save_state({"current": eps[2], "started": 0, "last_seen": 10})
        rotation.rebuild([self.show], duration_fn=lambda p: 1500)
        names = [n for n, _ in self.links()]
        self.assertNotIn("show", names)
        self.assertEqual(len(names), 6)
        parent = os.path.dirname(self.show.output_dir)
        self.assertEqual(os.listdir(parent), ["show"])

    def test_new_season_is_picked_up(self):
        os.makedirs(os.path.join(self.ssd, "show_s3"))
        open(os.path.join(self.ssd, "show_s3", "Show s3E1 Title.mp4"), "w").close()
        rotation.rebuild([self.show], duration_fn=lambda p: 1500)
        self.assertEqual([t for _, t in self.links()][5], "Show s3E1 Title.mp4")

    def test_track_records_changes_and_ignores_ads(self):
        eps = self.show.episodes()
        rotation.track([self.show], now=100, opened={eps[0]})
        rotation.track([self.show], now=160, opened={eps[0]})
        rotation.track([self.show], now=220, opened={"/somewhere/ad.mp4"})
        state = self.show.load_state()
        self.assertEqual((state["current"], state["started"], state["last_seen"]), (eps[0], 100, 160))
        rotation.track([self.show], now=280, opened={eps[1]})
        state = self.show.load_state()
        self.assertEqual((state["current"], state["previous"], state["started"]), (eps[1], eps[0], 280))

    def test_clock_jump_at_boot_does_not_skip_episode(self):
        eps = self.show.episodes()
        # Wall clock jumps a day forward (time sync) while only 5 minutes really pass.
        rotation.track([self.show], now=1000, opened={eps[1]}, clock=60, boot="b1")
        rotation.track([self.show], now=1000 + 86400, opened={eps[1]}, clock=360, boot="b1")
        rotation.rebuild([self.show], duration_fn=lambda p: 1500)
        self.assertEqual(self.links()[0][1], "Show s1E2 Title.mp4")

    def test_reboot_restarts_watch_time(self):
        eps = self.show.episodes()
        rotation.track([self.show], now=0, opened={eps[1]}, clock=100, boot="b1")
        rotation.track([self.show], now=1200, opened={eps[1]}, clock=1300, boot="b1")
        rotation.track([self.show], now=1300, opened={eps[1]}, clock=50, boot="b2")
        state = self.show.load_state()
        self.assertEqual((state["started_clock"], state["boot_id"]), (50, "b2"))
        self.assertEqual(rotation.resume_index(eps, state, lambda p: 1500), 1)

    def test_track_through_symlink_folder(self):
        rotation.rebuild([self.show], duration_fn=lambda p: 1500)
        link = os.path.join(self.show.output_dir, sorted(os.listdir(self.show.output_dir))[2])
        with open(link) as handle:
            opened = rotation.open_files()
            rotation.track([self.show], now=500, opened=opened)
        self.assertEqual(self.show.load_state()["current"], self.show.episodes()[2])


class ScheduleScanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = os.path.join(self.tmp.name, "forever_out.log")
        self.sched = os.path.join(self.tmp.name, "state", "schedule.json")
        self.write("2026-10-02T19:30:10: MAIN: [19:30:10] INFO  Schedule is changed to playlist Happy Days\n")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text, mode="a"):
        with open(self.log, mode) as f:
            f.write(text)

    def scan(self):
        return rotation.scan_schedule(self.log, self.sched)

    TV_OFF = "2026-10-02T22:01:00: MAIN: [22:01:00] INFO   **** turning tv OFF ****\n"

    def test_first_run_never_triggers(self):
        self.write(self.TV_OFF)
        self.assertFalse(self.scan())

    def test_scheduled_off_triggers_once(self):
        self.scan()
        self.write(self.TV_OFF + "2026-10-02T22:01:00: MAIN: [22:01:00] INFO  Schedule is changed to playlist TV_OFF\n")
        self.assertTrue(self.scan())
        self.write("2026-10-02T22:02:00: MAIN: [22:02:00] INFO  Schedule is changed to playlist TV_OFF\n")
        self.assertFalse(self.scan())
        self.assertEqual(rotation.read_json(self.sched)["playlist"], "TV_OFF")

    def test_off_from_deploy_triggers(self):
        self.scan()
        self.write("2026-10-03T01:06:30: SERVER: [01:06:30] INFO  *** sync initiated at server: 10/3/2026, 1:06:30 AM\n"
                   "2026-10-03T01:06:32: MAIN: [01:06:32] INFO  Clearing the resolution watch\n"
                   "2026-10-03T01:06:32: MAIN: [01:06:32] INFO   **** turning tv OFF ****\n"
                   "2026-10-03T01:06:38: MAIN: [01:06:38] INFO   **** HDMI off ****\n")
        self.assertTrue(self.scan())

    def test_tv_on_does_not_trigger(self):
        self.scan()
        self.write("2026-10-03T00:05:00: MAIN: [00:05:00] INFO   **** turning tv ON ****\n"
                   "2026-10-03T00:05:00: MAIN: [00:05:00] INFO  Schedule is changed to playlist Happy Days\n")
        self.assertFalse(self.scan())

    def test_off_and_on_within_one_minute_still_triggers(self):
        self.scan()
        self.write(self.TV_OFF + "x  **** turning tv ON ****\n")
        self.assertTrue(self.scan())

    def test_partial_line_is_read_next_time(self):
        self.scan()
        self.write("x MAIN: INFO   **** turning tv O")
        self.assertFalse(self.scan())
        self.write("FF ****\n")
        self.assertTrue(self.scan())

    def test_truncated_log_is_read_from_start(self):
        self.scan()
        self.write("x **** turning tv OFF ****\n", mode="w")
        self.assertTrue(self.scan())

    def test_missing_log_does_not_crash(self):
        os.remove(self.log)
        self.assertFalse(self.scan())


if __name__ == "__main__":
    unittest.main(verbosity=2)
