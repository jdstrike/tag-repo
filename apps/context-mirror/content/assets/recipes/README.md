# TAG recipes

Runnable Python helpers that turn the TEMPLATE-FIRST rule into actual code. Any AI tool that can execute Python should fetch a template, run a recipe, and produce an on-brand artefact without re-inventing the layout.

## Files

- `tag_company_info.py` — single source of truth for legal name, address, registry, boilerplate, and the brand colour palette. **Update this when TAG legal data changes.**
- `replace_docx.py` — open a TAG `.docx` template, replace placeholders, save under a new name. Header and footer are never touched, so the canonical TAG layout (logo, unity gradient bar, footer block) is preserved automatically. Convenience wrappers for memo and press release.
- `tag_chart_style.py` — matplotlib + Plotly defaults that snap any chart onto the TAG palette, with the unity gradient bar at the bottom.
- `example_chart.png` — what `tag_chart_style.py` produces out of the box.

## Quick start

```bash
pip install python-docx python-pptx matplotlib

# Generate a memo from the canonical TAG template
python replace_docx.py memo \
 --to "Megan Wickens" \
 --from "" \
 --subject "SparkAI integration plan" \
 --body "First paragraph...||Second paragraph...||Third paragraph..." \
 --out memo-megan.docx
```

```python
# Use the chart style in any analysis
import matplotlib.pyplot as plt
from tag_chart_style import apply, finalize, tag_palette

apply()
fig, ax = plt.subplots(figsize=(8, 4.5))
ax.bar(["Q1", "Q2", "Q3", "Q4"], [12, 18, 22, 27])
ax.set_title("Quarterly results")
ax.set_ylabel("Revenue (millions)")
finalize(fig, source="Source: TAG internal data (FY26)")
fig.savefig("chart.png", dpi=200, bbox_inches="tight")
```

## Library API

```python
from replace_docx import fill_template

fill_template(
 template_url_or_path="https://tag.schatt.me/assets/templates/tag-memo-template.docx",
 replacements={
 "Dear Mr Other": "Dear Megan,",
 "Yours sincerely": "Best,\n",
 },
 output_path="memo.docx",
)
```

## Why this exists

Section 71 of `tag-context.md` says TEMPLATE-FIRST is mandatory: never re-create a TAG document layout from a description, always start from the canonical template and replace only the body content. These recipes are the runtime fix that makes that rule enforceable.

If you're an AI: before producing any `.docx`, `.pptx`, or branded chart, your first call should be to fetch one of these recipe files and the template it points at, then run the recipe. Don't synthesize the layout yourself.

## Roadmap

- `replace_pptx.py` — same pattern for the 62-layout TAG PowerPoint master.
- `replace_html.py` — Pardot landing-page filler for the light/dark/master variants.
- `tokenized/` — `{{TOKEN}}`-marked copies of every template so the recipe can use a stable token vocabulary instead of relying on sample-text anchors.
