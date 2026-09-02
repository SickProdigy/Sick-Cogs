from runescape.utils import OSRS_SKILLS, clean_extract, parse_hiscores, wiki_page_url


def test_parse_hiscores_only_reads_skill_rows():
    text = "1,2277,4600000000\n2,99,200000000\n3,98,150000000\n1,50\n"
    rows = parse_hiscores(text, OSRS_SKILLS)
    assert rows == [
        {"name": "Overall", "rank": 1, "level": 2277, "experience": 4600000000},
        {"name": "Attack", "rank": 2, "level": 99, "experience": 200000000},
        {"name": "Defence", "rank": 3, "level": 98, "experience": 150000000},
    ]


def test_clean_extract_normalizes_and_truncates():
    assert clean_extract("  A\n\nshort   summary. ") == "A short summary."
    assert clean_extract("word " * 300, limit=30).endswith("…")
    assert len(clean_extract("word " * 300, limit=30)) <= 30


def test_wiki_page_url_quotes_titles():
    assert wiki_page_url("https://example.test", "Cook's Assistant") == (
        "https://example.test/w/Cook%27s_Assistant"
    )
