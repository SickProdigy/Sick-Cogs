import time
import unicodedata
from collections import OrderedDict
from difflib import SequenceMatcher
from urllib.parse import quote


def normalize_text(value):
    value=unicodedata.normalize("NFKD",str(value or "")).encode("ascii","ignore").decode("ascii").casefold()
    return " ".join("".join(character if character.isalnum() else " " for character in value).split())


def clean_text(value):
    return " ".join(str(value or "").replace(" ,",",").split())


def source_url(title):
    slug=quote(str(title or "").replace(" ","_"),safe="")
    return f"https://gray-zone-warfare.fandom.com/wiki/{slug}"


def record_page(record,data_version=""):
    name=clean_text(record.get("name") or record.get("id") or "Quest result")
    faction=clean_text(record.get("faction"));task_type=clean_text(record.get("task_type"))
    if not task_type and str(record.get("hidden_task","")).casefold()=="true":task_type="Hidden task"
    search_text=" ".join(clean_text(record.get(key)) for key in ("name","alternate","vendor","location","briefing","objectives"))
    return {
        "title":name,
        "fullurl":source_url(name),
        "vendor":clean_text(record.get("vendor")),
        "location":clean_text(record.get("location")),
        "type":task_type,
        "objectives":[clean_text(record.get("objectives"))] if clean_text(record.get("objectives")) else [],
        "guide":clean_text(record.get("briefing")),
        "faction_specific":bool(faction) or clean_text(record.get("location")).count(",")>=2,
        "faction":faction,
        "updated":str(data_version or ""),
        "image":str(record.get("image") or ""),
        "search_text":search_text,
    }


def page_score(query,page):
    wanted=normalize_text(query);title=normalize_text(page.get("title"));search=normalize_text(page.get("search_text") or title)
    if not wanted or not title:return 0
    wanted_tokens=set(wanted.split());search_tokens=set(search.split());overlap=len(wanted_tokens&search_tokens)/max(1,len(wanted_tokens))
    ratio=SequenceMatcher(None,wanted,title).ratio()
    if overlap<0.4 and wanted not in title and title not in wanted and ratio<0.68:return 0
    score=ratio*50+overlap*30
    if title==wanted:score+=100
    elif title.startswith(wanted) or wanted.startswith(title):score+=55
    elif wanted in title:score+=35
    return score


def rank_pages(query,pages,min_score=25):
    ranked=[(page_score(query,page),page) for page in pages]
    return [page for score,page in sorted(ranked,key=lambda item:(-item[0],normalize_text(item[1].get("title")))) if score>=min_score]


def parse_page(page):
    return {
        "title":clean_text(page.get("title") or "Quest result"),
        "url":str(page.get("fullurl") or ""),
        "vendor":clean_text(page.get("vendor")),
        "location":clean_text(page.get("location")),
        "type":clean_text(page.get("type")),
        "objectives":[clean_text(item) for item in page.get("objectives",[]) if clean_text(item)],
        "guide":clean_text(page.get("guide")),
        "faction_specific":bool(page.get("faction_specific")),
        "faction":clean_text(page.get("faction")),
        "updated":str(page.get("updated") or ""),
        "image":str(page.get("image") or ""),
    }


class TTLCache:
    def __init__(self,ttl=600,max_entries=128,clock=None):
        self.ttl=max(1,float(ttl));self.max_entries=max(1,int(max_entries));self.clock=clock or time.monotonic;self.data=OrderedDict()

    def get(self,key):
        item=self.data.get(key)
        if not item:return None
        if self.clock()-item[0]>=self.ttl:
            self.data.pop(key,None);return None
        self.data.move_to_end(key);return item[1]

    def set(self,key,value):
        self.data[key]=(self.clock(),value);self.data.move_to_end(key)
        while len(self.data)>self.max_entries:self.data.popitem(last=False)

    def clear(self):self.data.clear()
