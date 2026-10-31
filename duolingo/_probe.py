import sys, json
sys.path.insert(0, r"D:\Download\project\duolingo")
import duo_guard as g
cfg = g.load_config(); tok = cfg["jwt_token"]
st, data = g.http_json("GET", "https://stories.duolingo.com/api2/stories?fromLanguage=ru&learningLanguage=en", tok)
print("HTTP", st, "top keys:", list(data.keys()))
sets = data.get("sets")
print("sets type:", type(sets).__name__, "len:", len(sets) if hasattr(sets,'__len__') else '?')
if isinstance(sets, list) and sets:
    s0 = sets[0]
    print("set0 keys:", list(s0.keys()))
    stories = s0.get("stories")
    print("set0 stories:", type(stories).__name__, len(stories) if hasattr(stories,'__len__') else '?')
    if isinstance(stories, list) and stories:
        print("story0 keys:", list(stories[0].keys()))
        print("story0:", json.dumps({k: stories[0].get(k) for k in ('slug','title','id','setId')}, ensure_ascii=False)[:300])
elif isinstance(sets, dict) and sets:
    k0 = list(sets.keys())[0]
    print("set key:", k0, "value keys:", list(sets[k0].keys()) if isinstance(sets[k0], dict) else type(sets[k0]).__name__)
