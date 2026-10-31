import sys
sys.path.insert(0, r"D:\Download\project\duolingo")
import duo_guard as g
cfg = g.load_config(); tok = cfg["jwt_token"]
for url in ("https://stories.duolingo.com/api2/stories?fromLanguage=ru&learningLanguage=en",
            "https://stories.duolingo.com/api2/stories?fromLanguage=en&learningLanguage=ru"):
    st, data = g.http_json("GET", url, tok)
    items = []
    if isinstance(data, dict):
        for grp in data.get("sets") or []:
            if isinstance(grp, list): items.extend(grp)
    print(url.split("?")[1], "| HTTP", st, "| sets stories:", len(items))
    if items:
        print("   first 3:", [(i.get("id"), i.get("title"), i.get("xpToAward")) for i in items[:3]])
