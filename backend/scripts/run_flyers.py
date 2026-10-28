"""Run extraction on every image in a folder and print a summary.
  python -m scripts.run_flyers ../images/flyers
Results go next to the folder as results.json (gitignore it: it contains flyer text; publish accuracy numbers only)."""
import json
import pathlib
import sys
import time

from app.extraction import extract_flyer

folder = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "../images/flyers")
files = sorted(p for p in folder.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
if not files:
    raise SystemExit(f"No images found in {folder}")
results = {}
for f in files:
    t0 = time.time()
    try:
        d = extract_flyer(str(f))
        results[f.name] = d
        print(f"\n{f.name}  ({time.time() - t0:.1f}s)  type={d['listing_type']}")
        print(f"  title: {d['title']}")
        print(f"  date: {d['date']} ({d['date_status']})  raw: {d['date_text']!r}  availability: {d['availability_text']!r}")
        print(f"  time: {d['start_time']}  venue: {d['venue']}  phone: {d['contact_phone']}  alcohol: {d['serves_alcohol']}")
        for p in d["prices"]:
            print(f"  price: {(p['label'] or '')[:40]:40s} K{p['amount'] / 100:,.0f}")
        print(f"  needs confirmation: {d['needs_confirmation']}")
    except Exception as e:
        results[f.name] = {"error": repr(e)}
        print(f"\n{f.name}  ERROR: {e!r}")
out = folder.parent / "results.json"
out.write_text(json.dumps(results, indent=2, ensure_ascii=False))
print(f"\nSaved {out}")