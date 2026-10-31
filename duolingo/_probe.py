import sys, json
sys.path.insert(0, r"D:\Download\project\duolingo")
import duo_guard as g
cfg = g.load_config(); tok = cfg["jwt_token"]
uid = g.jwt_subject(tok)
tests = [
 ("stories ru->en", "https://stories.duolingo.com/api2/stories?fromLanguage=ru&learningLanguage=en"),
 ("stories no params", "https://stories.duolingo.com/api2/stories"),
 ("v2017 stories", "https://www.duolingo.com/2017-06-30/stories?fromLanguage=ru&learningLanguage=en"),
 ("v2017 stories list", "https://www.duolingo.com/2017-06-30/stories"),
 ("user stories", "https://www.duolingo.com/2017-06-30/users/%s/stories" % uid),
]
for name, url in tests:
    st, data = g.http_json("GET", url, tok)
    if isinstance(data, dict):
        shape = "dict keys=" + ",".join(list(data.keys())[:8])
    elif isinstance(data, list):
        shape = "list len=%d first_keys=%s" % (len(data), ",".join(list(data[0].keys())[:8]) if data and isinstance(data[0], dict) else "?")
    else:
        shape = type(data).__name__
    print("%-20s HTTP %-4s %s" % (name, st, shape[:160]))
