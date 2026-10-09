---
layout: default
title: Home
nav_order: 1
---

{%- comment -%}
The landing page reuses README.md so the two never drift apart.

jekyll-relative-links only rewrites links in a page's own source, not in
content pulled in by include_relative, so the README's relative links to
docs/*.md would otherwise 404 here. Rewriting the extensions after the capture
fixes them, and leaves README.md holding plain relative links that still work
in the GitHub file view.

docs/images/*.png is left alone: those are static files that Jekyll copies
unchanged, so the relative paths already resolve on the published site.

tools/ and the requirements files are excluded from the build, so the README's
links into them are pointed at GitHub instead.
{%- endcomment -%}

<details open markdown="block">
  <summary>On this page</summary>
  {: .text-delta }
- TOC
{:toc}
</details>

{% capture readme %}{% include_relative README.md %}{% endcapture %}
{{ readme
   | replace: 'docs/compatibility.md', 'docs/compatibility.html'
   | replace: 'docs/parity.md', 'docs/parity.html'
   | replace: '](tools/make_sample_data.py)', '](https://github.com/martinctc/opencode-dashboard/blob/main/tools/make_sample_data.py)'
   | replace: '](LICENSE)', '](https://github.com/martinctc/opencode-dashboard/blob/main/LICENSE)' }}

