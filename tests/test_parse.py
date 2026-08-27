import os
import shutil
import tempfile
import unittest

from donghak import extract_records, detect_total_pages, filter_records, fetch_page

HERE = os.path.dirname(__file__)
HTML = open(os.path.join(HERE, "sample_page.html"), encoding="utf-8").read()


class TestParse(unittest.TestCase):
    def test_extract_three(self):
        recs = extract_records(HTML)
        self.assertEqual(len(recs), 3)

    def test_name_parts(self):
        recs = extract_records(HTML)
        by = {r.name_kr: r for r in recs}
        self.assertEqual(by["백도홍"].name_hanja, "白道弘")
        self.assertIn("하동", by["백도홍"].region)

    def test_detect_pages(self):
        self.assertEqual(detect_total_pages(HTML), 3)

    def test_filter_region(self):
        recs = extract_records(HTML)
        gyeong = filter_records(recs, ["경상"], None)
        self.assertEqual(len(gyeong), 2)
        jeolla = filter_records(recs, ["전라"], None)
        self.assertEqual(len(jeolla), 1)

    def test_surname_filter(self):
        recs = extract_records(HTML)
        baek = [r for r in recs if r.name_kr.startswith("백")]
        self.assertEqual(len(baek), 2)
        self.assertNotIn("김이순", [r.name_kr for r in baek])


class TestFetch(unittest.TestCase):
    def test_cache_used(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "page_1.html")
            with open(os.path.join(HERE, "sample_page.html"), encoding="utf-8") as f:
                src = f.read()
            with open(p, "w", encoding="utf-8") as f:
                f.write(src)
            html = fetch_page("백", 1, d, use_cache=True)
            self.assertIn("백도홍", html)
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
