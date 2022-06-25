import unittest
from argparse import Namespace

from pylib.fields.text_field import TextField
from pylib.result import Result


class TestTextField(unittest.TestCase):
    def test_reconcile_01(self):
        """It handles an empty group."""
        group = [TextField(), TextField(), TextField()]
        self.assertEqual(
            TextField.reconcile(group),
            TextField(note="All 3 records are blank", result=Result.ALL_BLANK),
        )

    def test_reconcile_02(self):
        """It handles a unanimous match."""
        group = [
            TextField(value="Is same"),
            TextField(value="Is same"),
            TextField(value="Is same"),
        ]
        self.assertEqual(
            TextField.reconcile(group),
            TextField(
                note="Exact unanimous match, 3 of 3 records",
                value="Is same",
                result=Result.UNANIMOUS,
            ),
        )

    def test_reconcile_03(self):
        """It handles a tied match."""
        group = [
            TextField(value="Are same"),
            TextField(value="Is same"),
            TextField(value="Are same"),
            TextField(value="Is same"),
        ]
        self.assertEqual(
            TextField.reconcile(group),
            TextField(
                note="Exact match is a tie, 2 of 4 records with 0 blanks",
                value="Are same",
                result=Result.MAJORITY,
            ),
        )

    def test_reconcile_04(self):
        """It reports a majority match."""
        group = [
            TextField(value="Are same"),
            TextField(value="Is same"),
            TextField(value=""),
            TextField(value="Are same"),
        ]
        self.assertEqual(
            TextField.reconcile(group),
            TextField(
                note="Exact match, 2 of 4 records with 1 blank",
                value="Are same",
                result=Result.MAJORITY,
            ),
        )

    def test_reconcile_05(self):
        """It handles a normalized match resulting in all blanks."""
        group = [
            TextField(value="..@()"),
            TextField(value="??!@[]"),
            TextField(value=""),
        ]
        self.assertEqual(
            TextField.reconcile(group),
            TextField(
                note="All 3 normalized records are blank",
                value="",
                result=Result.NO_MATCH,
            ),
        )

    def test_reconcile_06(self):
        """It handles a unanimous normalized match."""
        group = [
            TextField(value="..@Good test()"),
            TextField(value="??Good test!@[]"),
            TextField(value="GOOD TEST"),
        ]
        self.assertEqual(
            TextField.reconcile(group),
            TextField(
                note="Normalized unanimous match, 3 of 3 records",
                value="??Good test!@[]",
                result=Result.UNANIMOUS,
            ),
        )

    def test_reconcile_07(self):
        """It handles a tied normalized match."""
        group = [
            TextField(value="..@Good test()"),
            TextField(value="??Good test!@[]"),
            TextField(value="#better not"),
            TextField(value="better not??"),
        ]
        self.assertEqual(
            TextField.reconcile(group),
            TextField(
                note="Normalized match is a tie, 2 of 4 records with 0 blanks",
                value="??Good test!@[]",
                result=Result.MAJORITY,
            ),
        )

    def test_reconcile_08(self):
        """It handles a normalized match."""
        group = [
            TextField(value="..@Good test()"),
            TextField(value="??Good test!@[]"),
            TextField(value="better not??"),
        ]
        self.assertEqual(
            TextField.reconcile(group),
            TextField(
                note="Normalized match, 2 of 3 records with 0 blanks",
                value="??Good test!@[]",
                result=Result.MAJORITY,
            ),
        )

    def test_reconcile_09(self):
        """It handles a fuzzy ratio match."""
        args = Namespace(fuzzy_ratio_threshold=90)
        group = [
            TextField(value=""),
            TextField(value="??Good test!@[] right here another"),
            TextField(value="Good is test[] right here another"),
        ]
        self.assertEqual(
            TextField.reconcile(group, args),
            TextField(
                note="Partial ratio match on 3 records with 1 blank, score=92",
                value="??Good test!@[] right here another",
                result=Result.FUZZY,
            ),
        )

    def test_reconcile_10(self):
        """It handles a fuzzy ratio match."""
        args = Namespace(fuzzy_ratio_threshold=90, fuzzy_set_threshold=50)
        group = [
            TextField(value=""),
            TextField(value="??Good test!@[] right here another"),
            TextField(value="Right here another good is test[]"),
        ]
        self.assertEqual(
            TextField.reconcile(group, args),
            TextField(
                note="Token set ratio match on 3 records with 1 blank, score=100",
                value="Right here another good is test[]",
                result=Result.FUZZY,
            ),
        )

    def test_reconcile_11(self):
        """It handles a fuzzy ratio match."""
        args = Namespace(fuzzy_ratio_threshold=90, fuzzy_set_threshold=50)
        group = [
            TextField(value=""),
            TextField(value="??Good test!@[] right here another"),
            TextField(value="Nothing matches"),
        ]
        self.assertEqual(
            TextField.reconcile(group, args),
            TextField(
                note="No text match on 3 records with 1 blank",
                value="",
                result=Result.NO_MATCH,
            ),
        )
