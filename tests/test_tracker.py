import email
import email.policy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tracker import base_id, parse_message


class TrackerTests(unittest.TestCase):
    def test_coauthor_submission(self):
        raw = b"""From: Neural Networks <em@example.com>\nDate: Sun, 12 Jul 2026 02:05:32 +0800\nSubject: Confirm co-authorship of submission to Neural Networks\nContent-Type: text/plain; charset=utf-8\n\nJournal: Neural Networks\nTitle: SpaCLV: Graph-preserving contrastive variational learning for cross-slice spatial transcriptomics domain identification\nManuscript Number: NEUNET-D-26-06007\nYou are listed as a co-author.\n"""
        result = parse_message(1, raw)
        self.assertIsNotNone(result)
        self.assertEqual(result.role, "co-author")
        self.assertEqual(result.topic, "Bioinformatics")
        self.assertEqual(result.base_manuscript_id, "NEUNET-D-26-06007")

    def test_review_invitation_is_ignored(self):
        raw = b"""From: Journal <x@example.com>\nSubject: Invitation to review a manuscript\n\nTitle: Not my paper\n"""
        self.assertIsNone(parse_message(2, raw))

    def test_revision_suffix(self):
        self.assertEqual(base_id("EAAI-D-26-9280R2"), "EAAI-D-26-9280")


if __name__ == "__main__":
    unittest.main()
