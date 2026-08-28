import os
import shutil
import tempfile
import unittest

from donghak import (
    extract_records,
    detect_total_pages,
    filter_records,
    fetch_page,
    annotate_generation,
    build_gen_map,
    participant_band,
    score_candidates,
    KINSHIP,
    group_by_generation,
    scan_corpus,
    Participant,
)

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


class TestLineage(unittest.TestCase):
    GEN = ["남", "기", "규", "형", "흠", "인", "낙", "창"]

    def test_annotate_generation(self):
        recs = [Participant("백형수", "白亨秀"), Participant("백도홍", "白道弘")]
        annotate_generation(recs, self.GEN)
        self.assertEqual(recs[0].generation, "형")
        self.assertEqual(recs[0].gen_index, 3)
        self.assertEqual(recs[1].generation, "")

    def test_group_by_generation(self):
        recs = [Participant("백형수", "白亨秀"), Participant("백도홍", "白道弘")]
        annotate_generation(recs, self.GEN)
        grp = group_by_generation(recs, self.GEN)
        self.assertIn("형", grp)
        self.assertEqual([r.name_kr for r in grp["형"]], ["백형수"])


class TestRanking(unittest.TestCase):
    GEN = ["영", "규", "종", "우", "상", "희형", "재", "호건", "제준", "동",
           "병심", "교세", "진", "구", "근", "섭", "준", "선", "태", "모",
           "현", "균", "용", "순", "병", "경걸", "기", "옥", "낙영", "식",
           "덕", "주기", "용일", "호수", "주채", "훈엽"]
    GEN_START = 35

    def test_score_ranks_sangjun_top(self):
        recs = [
            Participant("이배지", "", region="충청도 아산"),
            Participant("이상준", "", region="충청도 아산"),
            Participant("이영도", "", region="충청도 아산"),
        ]
        annotate_generation(recs, self.GEN, self.GEN_START)
        gmap = build_gen_map(self.GEN, self.GEN_START)
        band, _ = participant_band(1981)
        scored = score_candidates(recs, gmap, band, "아산", "이성호")
        self.assertEqual(scored[0][1].name_kr, "이상준")
        self.assertGreater(scored[0][0], scored[2][0])  # 이상준 > 이영도

    def test_participant_band_anchor(self):
        # 조상(1946) 앵커 → 참여자는 할아버지(2대 위) 세대
        band, gap = participant_band(1946)
        self.assertEqual(gap, 2)
        self.assertEqual(KINSHIP.get(gap), "할아버지")
        # 두 생년으로 간격 추정(35년)해도 2대 위 유지
        _, gap2 = participant_band(1946, interval=35)
        self.assertEqual(gap2, 2)
        # 조회자(1981) 앵커 → 증조할아버지(3대 위)
        _, gap3 = participant_band(1981)
        self.assertEqual(gap3, 3)


class TestLiterature(unittest.TestCase):
    def test_scan_corpus(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "paper.txt"), "w", encoding="utf-8") as f:
                f.write("기록에는 백도홍 白道弘 이 참여했다. 다른 사람 김철수 金鐵秀 도 있다.")
            recs = scan_corpus(d, "백")
            names = [r.name_kr for r in recs]
            self.assertIn("백도홍", names)
            self.assertNotIn("김철수", names)
            self.assertTrue(all(r.source == "문헌" for r in recs))
        finally:
            shutil.rmtree(d)


class TestFetch(unittest.TestCase):
    def test_cache_used(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "page_%EB%B0%B1_1.html")
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
