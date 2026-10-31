import sys, json
sys.path.insert(0, r"D:\Download\project\duolingo")
import duo_guard as g
cfg = g.load_config(); tok = cfg["jwt_token"]
st, data = g.http_json("GET", "https://stories.duolingo.com/api2/stories?fromLanguage=ru&learningLanguage=en", tok)
sets = data["sets"]
s0 = sets[0]
print("sets[0] type:", type(s0).__name__, "len:", len(s0))
print("sets[0] first items types:", [type(x).__name__ for x in s0[:4]])

# walk recursively to find dicts with a 'slug' key
found = []
def walk(node, depth=0):
    if depth > 4 or len(found) >= 5: return
    if isinstance(node, dict):
        if "slug" in node: found.append(node); return
        for v in node.values(): walk(v, depth+1)
    elif isinstance(node, list):
        for v in node[:5]: walk(v, depth+1)
walk(sets)
print("found slugs:", [ (f.get("slug"), f.get("title")) for f in found ])
# show one set shallow
print("sets[0][:2]:", json.dumps(s0[:2], ensure_ascii=False)[:400])
