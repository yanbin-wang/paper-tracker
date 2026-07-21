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

    def test_malformed_message_id_does_not_abort_scan(self):
        raw = b"""From: Microsoft Notifications <notify@microsoft.com>\nDate: Sun, 12 Jul 2026 02:05:32 +0800\nSubject: Confirming your submission\nMessage-ID: [broken-id@microsoft.com]>\nContent-Type: text/plain; charset=utf-8\n\nJournal: Bioinformatics\nTitle: Reliable Protein Function Prediction with Transformers\nManuscript Number: BIOINF-D-26-01234\nThank you for submitting.\n"""
        result = parse_message(3, raw)
        self.assertIsNotNone(result)
        self.assertEqual(result.message_id, "[broken-id@microsoft.com]>")

    def test_reviewer_request_is_not_an_authored_submission(self):
        raw = b"""From: Data Editorial Office <data@mdpi.com>\nSubject: [Data] Manuscript ID: data-4423166 - Review Request Reminder\n\nTitle: A paper written by somebody else\nDecision: rejected\n"""
        self.assertIsNone(parse_message(4, raw))

    def test_submission_confirmation_is_not_misclassified_as_accepted(self):
        raw = b"""From: Pattern Recognition <em@editorialmanager.com>\nDate: Sun, 12 Jul 2026 02:05:32 +0800\nSubject: PR-D-26-09497 - Confirming your submission to Pattern Recognition\n\nJournal: Pattern Recognition\nTitle: Distilling Large Language Models into Lightweight Detectors\nManuscript Number: PR-D-26-09497\nPlease confirm that you accept the terms.\n"""
        result = parse_message(5, raw)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "submitted")
        self.assertEqual(result.venue, "Pattern Recognition")

    def test_folded_review_invitation_is_ignored(self):
        raw = b"""From: Blockchains Editorial Office <blockchains@mdpi.com>\nSubject: [Blockchains] Reminder for Invitation to\n Review for Blockchains\n\nTitle: Breaking the Peg, or Not\nPlease view your submission in the review system.\n"""
        self.assertIsNone(parse_message(6, raw))

    def test_editorial_sender_supplies_complete_venue(self):
        raw = b"""From: IEEE Transactions on Information Forensics and Security <no-reply@researchexchange.com>\nSubject: Action Recommended: View your submission to IEEE Transactions on\n Information Forensics and Security\n\nTitle: Beyond Fixed Fusion for Phishing Detection\n"""
        result = parse_message(7, raw)
        self.assertIsNotNone(result)
        self.assertEqual(result.venue, "IEEE Transactions on Information Forensics and Security")


if __name__ == "__main__":
    unittest.main()
