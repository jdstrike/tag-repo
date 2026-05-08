---
title: "TAG Brand Design System Playbook"
description: "Reproducible workflow to build, deploy, and operate a complete brand and AI context for the Adecco Group. Adapted from the Autismus Stiftung playbook (v1.0, 2026-04-30)."
date: 2026-04-30
version: 1.0
canonical_url: "https://context.tag-repo.com/playbook.md"
applies_to: "the Adecco Group (institutional), Adecco + LHH + Akkodis"
---

# TAG Brand Design System Playbook

> Reproducible workflow that turns scattered TAG brand materials into a single hub at context.tag-repo.com, consumable by any AI tool inside the Group (Microsoft Copilot, Claude, ChatGPT, Gemini, internal RAG) and by any human running TAG-branded work. This playbook documents what was built, in what order, with which tools, and which files. The same shape transfers to any other brand.

## 0. End state per brand

Eight connected layers form a complete brand design system. For TAG, the layers map to these URLs:

| Layer | What it is | TAG URL |
|---|---|---|
| 1. Brand Context | Markdown master with strategy, narrative, voice, persona, tokens, components, templates, governance | `/tag-context.md` |
| 2. AI Index | llmstxt.org-standard index pointing at every brand source | `/llms.txt`, `/llms-full.txt` |
| 3. Design Tokens | CSS Custom Properties + DTCG-JSON for colour, typography, spacing, radii | `/assets/tokens/` |
| 4. Hub Page | Single-source-of-truth HTML with prompt-adapter, mission, voice, identity, downloads, governance | `/` (index.html) |
| 5. Component Library | Live previews of every web component plus copy-paste code | `/design-system/components/` |
| 6. Document Library | Office templates (DOCX, PPTX) with PNG previews and runnable Python recipes | `/design-system/documents/` |
| 7. Logo Gallery | Every logo variant on multiple background tints, click to download | `/design-system/logos/` |
| 8. Recipes | Runnable Python that opens a template, replaces placeholders, saves a finished file | `/assets/recipes/` |

The Autismus Stiftung used a WordPress block-pattern PHP file for layer 8. TAG runs on Pardot landing pages and on `@adeccoux/tag-ds` v4.9.0 for product UI, so layer 8 becomes runnable Python recipes plus a pointer at the React component library.

## 1. Discovery

What you have before you start.

The TAG inputs that fed this hub:

* TAG Brand Guidelines V7 (June 2025), 85-page PDF.
* TAG Narrative document (November 2025).
* Corporate Presentation (February 2025).
* Tone of Voice Guide (March 2026), the source of the four pairs (clear and credible, inclusive and approachable, energetic and optimistic, curious and courageous).
* TAG Prompt Card (April 2026), US English, brand language list, before-writing and before-finalizing checklists.
* Eighteen document templates: memo, press release, letterhead (Zurich HQ), Word-doc multi-page, RFP short, RFP long, boilerplate, email signature, envelope, two newsletter headers, four social-media formats, three Pardot landing-page variants, and the 62-layout PowerPoint master.
* Logo files: family lockup colour positive, colour negative (SVG and PNG), white, black, plus the unity gradient bar and the unity sphere.

The discovery rule transfers verbatim: pull every existing brand asset before building anything new.

## 2. Brand Context: the markdown heart

`/tag-context.md` is the single source of truth that every AI tool reads before producing a TAG artefact. 76 numbered sections grouped into seven parts (Strategy & narrative, Voice & messaging, Visual identity, Portfolio & operations, Proof points, Application, AI governance & SparkAI), plus the web component library, the document pattern library, the template registry, and as of v4.1.0 the canonical Prompt Card in Section 75.

Key structural decisions:

The file is self-contained. No external fetches at runtime. If something is missing, mirror it to `context.tag-repo.com/assets/` and update the file.

Every section has a stable anchor (`## NN. title`) so the AI index can deep-link.

YAML front-matter carries `version`, `last_updated`, `applies_to`, `intended_consumers`, and `governance`. Bumping the version is part of every meaningful update.

UTF-8 is enforced. The file is served as `text/markdown; charset=utf-8` by the nginx container (configured in `nginx.conf`), so umlauts and curly quotes do not get mangled.

## 3. AI Index: llms.txt and llms-full.txt

`/llms.txt` is the curated entry point for any agent that follows the llmstxt.org convention. About 4 KB. Lists the master, the prompt card, the visual identity assets, the template registry, and the governance link. Agents that respect the convention read this first.

`/llms-full.txt` is the same content as `/tag-context.md` served as plain text. Some tools refuse Markdown MIME types; they get the same content this way.

`/tag-context.txt` is a third copy of the master under a `.txt` extension, again because some tools (notably older Microsoft Copilot Studio knowledge connectors) only accept `.txt` for non-tabular knowledge sources.

Single canonical URL per file. No mirroring under `/design-system/`. The hub references these absolute paths so the AI never has to guess which copy is current.

## 4. Design tokens

`/assets/tokens/tokens.css` ships every brand variable as a CSS custom property: TAG Turquoise (`#5CB8B2`), Primary 500 (`#1C304B`), the unity gradient, semantic surfaces, button states, the spacing scale, and the type ramp. The hub's HTML pages and any consumer site can reference them with `var(--…)`.

`/assets/tokens/tokens.json` is the same data in DTCG format for tooling (Style Dictionary, Token Studio, Figma plugins, Storybook).

The unity gradient is the canonical 6-stop gradient. Always horizontal, always at the bottom of a layout, never the top, never recoloured, never resized in height:

```
linear-gradient(90deg,
 #2DBFB8 0%,
 #1A7BAD 22%,
 #6B2D8B 44%,
 #E30613 63%,
 #F05A28 81%,
 #F9B233 100%);
```

## 5. Hub page

`/` (`index.html`) is the human-facing front door. It opens with the prompt-adapter (Magenta-equivalent: TAG Turquoise top border, dark navy background, copy-button), then steps through Strategy, Voice, Identity, Portfolio, Proof, Governance, and Downloads, and ends with a live context viewer that fetches `tag-context.md` on demand.

The prompt-adapter is the most important component on the page. It is the first thing a visitor sees, and it is the only thing they need to grab to use TAG voice in any AI tool. The pre-formatted block contains:

```
You are writing for the Adecco Group. Read https://context.tag-repo.com/tag-context.md
and apply the canonical Prompt Card at https://context.tag-repo.com/prompt-card.md
before producing any artefact.

Apply the four-pair voice ... [voice rules]
Tone of voice (all four binding) ... [pairs]
Brand language ... [terms]
Tagline verbatim: "Making the future work for everyone".

For .docx / .pptx / Pardot landing pages: TEMPLATE-FIRST. Fetch the canonical
template from https://context.tag-repo.com/assets/templates/index.json and use it as
the container. Do not synthesize the layout from a description.

Now help me with: [INSERT YOUR TASK]
```

A copy-button writes the block to the clipboard. Three small links sit beside it: prompt-card.md, tag-context.md, llms.txt.

## 6. Component library

`/design-system/components/index.html` renders every TAG web component live in the page (header, classic hero, card grid, FAQ accordion, primary and secondary buttons, full-bleed gradient CTA, footer with the unity gradient bar at the bottom). Each entry has a live preview, copy-paste HTML, and a spec block listing tokens, variants, and don't-do rules.

For React product UI, the page hands off to `@adeccoux/tag-ds` v4.9.0 (Storybook at `dev.tagds.adeccogroup.com`). For Pardot, it hands off to the three landing-page variants in the document library. The component library is for vanilla HTML and Pardot, where pulling in a React build is overkill.

## 7. Document library

`/design-system/documents/index.html` is a tile grid of every TAG document template, each rendered as a PNG preview generated from the actual file (LibreOffice headless DOCX → PDF, then `pdftoppm` PDF → PNG). The grid is split into Word documents, Print and physical, Social media, and Web (Pardot landing pages).

Each tile links to the underlying file, the PDF preview where one exists, and the TAG Family lockup variant (`tagfam-*`) for cross-brand documents.

The tile section is followed by a runnable code block that shows the exact CLI invocation to generate a memo from the template:

```
python replace_docx.py memo \
 --to "Megan Wickens" \
 --from "" \
 --subject "SparkAI integration plan" \
 --body "First paragraph...||Second paragraph..." \
 --out memo-megan.docx
```

## 8. Logo gallery

`/design-system/logos/index.html` shows every family lockup variant on four background tints (white, Grey 100, Primary 500, unity gradient) so contrast is testable at a glance. The unity gradient bar and the unity sphere appear at the bottom of the page as the two graphic devices that travel with the brand.

A direct-links table follows the gallery for users who know exactly which file they need.

A red warning box repeats the binding don'ts: no recolouring, no rotating, no distortion, no symbol-alone, no colour-positive on dark, no colour-negative on light. The brand book remains the binding reference.

## 9. Recipes

`/assets/recipes/` is the runnable Python that turns the TEMPLATE-FIRST rule from text into code. Three files matter:

`tag_company_info.py` — the central data dict. Legal name, registered address (Bellerivestrasse 30, 8008 Zurich), phone, press office, registry, stock exchange ticker, headcount, the approved short and long boilerplate paragraphs, the GBU one-liners, and the brand colour palette. When TAG legal data changes, this file is the only thing that needs editing.

`replace_docx.py` — fetches a template from `context.tag-repo.com/assets/templates/`, replaces placeholders in the body only (header and footer are never touched, so the canonical TAG layout is preserved), saves under a new name. Convenience wrappers for memo and press release; library API (`fill_template`) for everything else.

`tag_chart_style.py` — matplotlib and Plotly defaults that snap any chart onto the TAG palette (Primary 500 axes, Grey 100 gridlines, the 6-stop unity gradient as the colour cycle), with the unity gradient bar at the bottom of the figure. One `apply()` call sets every rcParam; one `finalize(fig, source=...)` call adds the source line and the gradient bar.

The pattern is faithful to the Autismus playbook's `_footer_builder.py`: a single helper that injects a uniform TAG footer (or in chart terms, a uniform unity bar) so visual consistency does not depend on whoever is generating the artefact.

## 10. AI governance

Sections 35-46 of `tag-context.md` cover SparkAI's five Responsible AI Principles (Ethical, Human-Centric, Transparent, Safe, Lawful), approval and review gates, source-of-truth resolution, confidentiality classifications, and banned and blocked patterns.

The before-writing and before-finalizing checklists from the Prompt Card (Section 75.6 and 75.7) are the runtime equivalent of the Autismus six-check list. Seven questions before writing (audience, channel, mode, outcome, voice anchor, brand language, truth). Eight checks before finalizing (US English, sentence case, contractions, brand name, tagline emphasis, brand-language phrase present, no banned patterns, all four tone pairs satisfied).

Confidentiality follows the Group classification scheme. Personal data of clients, candidates, employees never enters an AI prompt unless the AI is sanctioned by SparkAI and the data is classified accordingly.

## 11. Reproducibility for any other brand

Swap the markdown master for the new brand's narrative, swap the tokens, swap the logos, swap the templates, swap the company info dict. The structure (8 layers, the URL conventions, the prompt-adapter pattern, the recipe pipeline) does not change.

What is interchangeable: brand name, brand colours, logos, persona definitions, board list, address, live footer, Office templates.

What is not interchangeable: the architecture (8 layers), the file structure (`/brand-context.md`, `/design-system/`, `/design-system/components/`, `/design-system/documents/`, `/design-system/logos/`, `/assets/recipes/`), the token-naming convention (`--tag-*` for TAG, `--brand` as a semantic alias), the prompt-adapter pattern, and the AI governance check sequence.

Order of work, one person, roughly five days:

| Day | Output |
|---|---|
| 1 | Discovery + live-site analysis + brand-context.md scaffolded |
| 2 | Design tokens + hub page with prompt-adapter |
| 3 | Component library + live-footer carry-over |
| 4 | Document templates with replace_docx recipe + chart style |
| 5 | Logo gallery + AI index + deploy + verification |

## 12. File inventory of a finished TAG hub

```
/tag-context.md 76 sections, 138 KB
/tag-context.txt plain-text mirror
/llms.txt curated AI index
/llms-full.txt full content as plain text
/prompt-card.md standalone copy-paste card
/context.html SSR HTML mirror
/index.html hub landing page (with prompt-adapter)
/playbook.md this file
/robots.txt default-allow with content signals
/design-system/
 components/index.html live previews + copy-paste code
 documents/index.html template grid with PNG previews
 logos/index.html logo gallery on multiple tints
/assets/
 templates/ 18 template files + index.json manifest
 recipes/ tag_company_info.py, replace_docx.py,
 tag_chart_style.py, README.md, example_chart.png
 previews/ PNG previews of every template
 tokens/ tokens.css, tokens.json
 logos/ SVGs and PNGs of family lockup + graphic devices
 fonts/ Open Sauce Sans
 docs/ brand-guidelines.pdf, ppt-template.pptx
 vendor/ third-party assets when needed
```

About 60 files for a complete system, of which 18 are the document templates and 18 are their PNG previews.

## 13. Tool stack

| Tool | Purpose |
|---|---|
| Python 3.10+ | Recipe pipeline, deploy script, footer builder |
| python-docx | DOCX manipulation |
| python-pptx | PowerPoint manipulation |
| matplotlib | Chart generation with `tag_chart_style` |
| LibreOffice headless | DOCX → PDF for previews |
| pdftoppm (Poppler) | PDF → PNG for previews |
| paramiko | SSH/SFTP deploy to Unraid |
| nginx 1.27-alpine | Static hosting on Unraid |
| Cloudflare Tunnel | Public hostname for context.tag-repo.com |
| python-markdown | Markdown → SSR HTML for context.html |

All open source. No paid tools needed.

## 14. What to avoid

* More than one canonical URL per file. AI tools cannot tell which is current.
* Hard-coded hex values in templates. They prevent a global theme switch.
* Global find-and-replace in DOCX without context. `<w:t>1</w:t>` will also overwrite page-number fields.
* Footer-clear that only removes paragraphs. If the original footer is a table, it stays and the new footer stacks on top.
* Touching `.htaccess` without a backup. Apache returns 500 instantly on a syntax error.
* Linking to PNG previews that do not exist yet. Dead links destroy trust.
* Carrying brand voice rules into Section 0.4 of the master without also putting them in the Prompt Card. The 138 KB file does not always travel; the 4 KB card does.
* Letting templates and the master drift. When a new template lands in `/assets/templates/`, add it to `index.json` in the same commit.

## 15. What worked

* Brand context as Markdown, not PDF. AI tools can fetch it live and keep up.
* llms.txt convention rather than a custom one. Tooling support grows.
* CSS variables with a double-aliasing layer (`--tag-turquoise` and `--brand`). The TAG-DS React conventions and the Pardot landing-page templates can both consume the same tokens.
* Office templates with a Python recipe rather than hard-coded body. Brief workflow collapses to one CLI call.
* Real PNG previews rendered from the templates rather than schematic mocks. Stakeholders trust them immediately.
* Prompt-adapter with a copy-button at the top of the hub. The single most-used UI element.
* TEMPLATE-FIRST as both a written rule (Section 71) and a runtime helper (`replace_docx.py`). The rule alone is words on a page; the helper is what makes AI tools actually use the templates.

## 16. Maintenance

When the TAG narrative changes, edit `tag-context.md` first, then run the SSR HTML regenerator (`render_context_html.py`), then mirror to `/tag-context.txt` and `/llms-full.txt`, then bump the version, then deploy.

When a template changes, drop the new file into `/assets/templates/`, add or update its entry in `index.json`, regenerate its PNG preview, and update the document library page if the headline metadata changed.

When tokens change, edit both `tokens.css` and `tokens.json` (they are independent, do not auto-sync), then deploy. The hub picks up the new values without a rebuild.

When the legal entity changes (new HQ, new CEO, new ticker), edit `tag_company_info.py` and re-run the deploy script. Anything generated through the recipes will pick up the new data automatically.

## 17. Licence and use

This methodology is free to reuse. Adopting it for another brand takes one person with Python basics, Design Systems affinity, and access to the brand's source materials about five working days.

Source documentation of the system this playbook describes: [context.tag-repo.com/tag-context.md](https://context.tag-repo.com/tag-context.md), [context.tag-repo.com/prompt-card.md](https://context.tag-repo.com/prompt-card.md), [context.tag-repo.com/llms.txt](https://context.tag-repo.com/llms.txt).

Original methodology source: Autismus Stiftung Brand Design System Playbook v1.0 (April 2026).
