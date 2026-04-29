#!/usr/bin/env python3
"""Translate landing/index.html to Russian using DeepL API.

Produces landing/ru/index.html with:
- All visible text translated
- SEO meta tags translated
- OG/Twitter meta translated
- JSON-LD translated
- hreflang tags added
- lang="ru" on <html>
- Language switcher updated (ru active)
- Share URLs updated for /ru/ path

Usage:
    DEEPL_API_KEY=xxx python3 scripts/translate_landing.py
"""

import json
import os
import re
import sys
from pathlib import Path

import deepl
from bs4 import BeautifulSoup, Comment, NavigableString

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "landing" / "index.html"
DST = REPO_ROOT / "landing" / "ru" / "index.html"

API_KEY = os.environ.get("DEEPL_API_KEY", "")
if not API_KEY:
    sys.exit("Set DEEPL_API_KEY environment variable")

translator = deepl.Translator(API_KEY)

# ── Tags/attrs to skip ───────────────────────────────────────────────────────
SKIP_TAGS = {"script", "style", "code", "pre", "svg", "math", "noscript"}

# Patterns that should NOT be translated (purely technical, not sentences)
PRESERVE_PATTERNS = re.compile(
    r"^(?:PulseAudio|PipeWire|Bluetooth|Docker|LXC|Proxmox|Waitress|Flask|"
    r"HAOS|mDNS|A2DP|GitHub|MIT|Cloudflare|Google|"
    r"\xa9|\u2192|\u2190|\u2197|\u2022|\u2014|\.{2,}|https?://\S+)$",
    re.IGNORECASE,
)

# ── Collect translatable strings ─────────────────────────────────────────────
char_count = 0


def is_translatable(text: str) -> bool:
    """Check if text contains translatable content."""
    stripped = text.strip()
    if not stripped or len(stripped) < 2:
        return False
    if PRESERVE_PATTERNS.match(stripped):
        return False
    # Pure emoji / punctuation / numbers
    if re.match(r"^[\s\d\W]*$", stripped) and not re.search(r"[a-zA-Z]", stripped):
        return False
    return True


def translate_batch(texts: list[str]) -> list[str]:
    """Translate a batch of texts via DeepL with brand protection."""
    if not texts:
        return []
    protected = [_protect_brands(t) for t in texts]
    has_brands = any("<keep>" in p for p in protected)
    if has_brands:
        protected = [re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;)", "&amp;", p) for p in protected]
        results = translator.translate_text(
            protected,
            target_lang="RU",
            tag_handling="xml",
            ignore_tags=["keep"],
        )
    else:
        results = translator.translate_text(texts, target_lang="RU")
    if isinstance(results, list):
        return [_unprotect_brands(r.text) for r in results]
    return [_unprotect_brands(results.text)]


def translate_text(text: str) -> str:
    """Translate a single string with brand protection."""
    protected = _protect_brands(text)
    has_brands = "<keep>" in protected
    if has_brands:
        # Escape bare & for XML validity
        protected = re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;)", "&amp;", protected)
        result = translator.translate_text(
            protected,
            target_lang="RU",
            tag_handling="xml",
            ignore_tags=["keep"],
        )
        return _unprotect_brands(result.text)
    else:
        result = translator.translate_text(text, target_lang="RU")
        return result.text


# ── Manual overrides for key phrases ─────────────────────────────────────────
MANUAL_OVERRIDES = {
    "Make Any Speaker Smart": "Сделайте любую колонку умной",
    "Build Your Own Multi-Room Audio": "Создайте свою multiroom-аудиосистему",
    "Spread the Word": "Расскажите друзьям",
    "Share on": "Поделиться в",
    "Copy link": "Копировать ссылку",
    "Copied!": "Скопировано!",
    "Select language": "Выберите язык",
    "More languages…": "Другие языки…",
    "Get Started — It's Free": "Начать — это бесплатно",
    "View on GitHub": "Открыть на GitHub",
    "Full Documentation": "Полная документация",
    "Home": "Главная",
    "Features": "Возможности",
    "How It Works": "Как это работает",
    "Screenshots": "Скриншоты",
    "Get Started": "Начать",
    "Free & Open Source": "Бесплатно и с открытым исходным кодом",
    "Quick Start": "Быстрый старт",
}

# Brand names and terms that must NOT be translated.
# We wrap them in <keep> XML tags before sending to DeepL
# and restore after. DeepL's tag_handling="xml" preserves tag content.
BRAND_TERMS = [
    "Sendspin Bluetooth Bridge",
    "Open Home Foundation",
    "Music Assistant",
    "Home Assistant",
    "Home Automation",
    "Bluetooth Bridge",
    "Sendspin",
    "Multiroom",
    "Multi-Room",
    "multiroom",
    "multi-room",
]

# Sort longest first to avoid partial matches
BRAND_TERMS.sort(key=len, reverse=True)
_BRAND_RE = re.compile(
    r"(" + "|".join(re.escape(t).replace(r"\ ", r"[\s\xa0]+") for t in BRAND_TERMS) + r")",
    re.IGNORECASE,
)


def _protect_brands(text: str) -> str:
    """Wrap brand terms in <keep> tags so DeepL preserves them."""

    def _repl(m):
        return f"<keep>{m.group(0)}</keep>"

    return _BRAND_RE.sub(_repl, text)


def _unprotect_brands(text: str) -> str:
    """Remove <keep> wrapper tags from translated text."""
    return re.sub(r"<keep>(.*?)</keep>", r"\1", text)


def apply_override(text: str) -> str | None:
    """Check manual overrides (case-insensitive)."""
    for en, ru in MANUAL_OVERRIDES.items():
        if en.lower() in text.lower():
            return text.replace(en, ru).replace(en.lower(), ru)
    return None


# ── Main translation logic ───────────────────────────────────────────────────
def main():
    global char_count

    html = SRC.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")

    # 1. Update <html lang>
    html_tag = soup.find("html")
    if html_tag:
        html_tag["lang"] = "ru"

    # 2. Translate <title>
    title_tag = soup.find("title")
    if title_tag and title_tag.string:
        title_tag.string = translate_text(title_tag.string)
        char_count += len(title_tag.string)

    # 3. Translate meta tags
    meta_translate = {
        "description": True,
        "keywords": True,
    }
    # og:site_name should not be translated (brand name)
    SKIP_META_PROPS = {"og:site_name"}
    for meta in soup.find_all("meta"):
        name = meta.get("name", "")
        prop = meta.get("property", "")
        content = meta.get("content", "")
        if not content:
            continue

        should_translate = False
        if prop in SKIP_META_PROPS:
            should_translate = False
        elif (
            name in meta_translate
            or prop
            in (
                "og:title",
                "og:description",
                "og:image:alt",
            )
            or name in ("twitter:title", "twitter:description", "twitter:image:alt")
        ):
            should_translate = True

        if should_translate and is_translatable(content):
            char_count += len(content)
            meta["content"] = translate_text(content)

        # Update og:locale
        if prop == "og:locale":
            meta["content"] = "ru_RU"
        if prop == "og:locale:alternate":
            meta["content"] = "en_US"

    # 4. Add hreflang links
    canonical = soup.find("link", rel="canonical")
    if canonical:
        canonical["href"] = canonical["href"].rstrip("/") + "/ru/"
        # Add hreflang tags after canonical
        for lang, path in [("en", "/"), ("ru", "/ru/"), ("x-default", "/")]:
            hreflang = soup.new_tag(
                "link",
                rel="alternate",
                hreflang=lang,
                href=f"https://sendspin-bt-bridge.pages.dev{path}",
            )
            canonical.insert_after(hreflang)

    # 5. Translate JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            if "description" in data:
                char_count += len(data["description"])
                data["description"] = translate_text(data["description"])
            if "name" in data and data["name"] != "Sendspin Bluetooth Bridge":
                char_count += len(data["name"])
                data["name"] = translate_text(data["name"])
            data["url"] = data.get("url", "").rstrip("/") + "/ru/"
            data["inLanguage"] = "ru"
            script.string = json.dumps(data, ensure_ascii=False, indent=4)
        except (json.JSONDecodeError, TypeError):
            pass

    # 6. Collect all visible text nodes for batch translation
    text_nodes = []
    for element in soup.find_all(string=True):
        if isinstance(element, Comment):
            continue
        if element.parent and element.parent.name in SKIP_TAGS:
            continue
        text = element.strip()
        if is_translatable(text):
            text_nodes.append(element)

    # Batch translate in chunks of 50
    batch_size = 50
    for i in range(0, len(text_nodes), batch_size):
        batch = text_nodes[i : i + batch_size]
        originals = [str(node).strip() for node in batch]

        # Check overrides first
        translations = []
        to_translate_idx = []
        to_translate_texts = []
        for j, orig in enumerate(originals):
            override = apply_override(orig)
            if override:
                translations.append(override)
            else:
                translations.append(None)
                to_translate_idx.append(j)
                to_translate_texts.append(orig)

        # Batch translate non-overridden
        if to_translate_texts:
            char_count += sum(len(t) for t in to_translate_texts)
            api_results = translate_batch(to_translate_texts)
            for k, idx in enumerate(to_translate_idx):
                translations[idx] = api_results[k]

        # Replace in DOM
        for j, node in enumerate(batch):
            if translations[j]:
                original_str = str(node)
                leading = original_str[: len(original_str) - len(original_str.lstrip())]
                trailing = original_str[len(original_str.rstrip()) :]
                node.replace_with(NavigableString(leading + translations[j] + trailing))

    # 7. Update language switcher — make RU active
    for opt in soup.find_all("button", class_="lang-opt"):
        data_lang = opt.get("data-lang", "")
        if data_lang == "ru":
            opt["class"] = ["lang-opt", "active"]
        elif "active" in opt.get("class", []):
            classes = [c for c in opt["class"] if c != "active"]
            opt["class"] = classes

    # 8. Update share URLs to /ru/ path
    base_url = "https://sendspin-bt-bridge.pages.dev/"
    ru_url = "https://sendspin-bt-bridge.pages.dev/ru/"
    for a in soup.find_all("a", class_="share-btn"):
        href = a.get("href", "")
        if base_url in href and "/ru/" not in href:
            a["href"] = href.replace(base_url, ru_url)

    # 9. Update navigation link to point back to EN version
    # Add a lang switcher that links to /
    for a in soup.find_all("a", class_="nav-link"):
        href = a.get("href", "")
        if href.startswith("#"):
            continue

    # 10. Write output and post-process
    DST.parent.mkdir(parents=True, exist_ok=True)
    output = str(soup)

    # Post-process: fix DeepL artifacts
    for term in BRAND_TERMS:
        output = output.replace("\u00ab" + term + "\u00bb", term)
        output = output.replace("\u201e" + term + "\u201c", term)

    # Fix "Главная" that should be "Home" in brand contexts
    output = output.replace("Главная Assistant", "Home Assistant")
    output = output.replace("Главная Foundation", "Home Foundation")
    output = output.replace("Open Главная", "Open Home")
    output = output.replace("smart Главная", "smart home")
    output = output.replace("your Главная", "your home")

    # Fix broken brand references
    output = output.replace("Bluetooth-мост Sendspin", "Sendspin Bluetooth Bridge")
    output = output.replace("Sendspin Bluetooth-мост", "Sendspin Bluetooth Bridge")
    output = output.replace("Помощник по музыке", "Music Assistant")
    output = output.replace("плеерMulti-Room", "плеер Multi-Room")
    output = output.replace("Sendspin Синхронизация", "Sendspin Sync")

    # Second pass: translate remaining English sentences via DeepL
    # Find lines that are mostly English and re-translate them
    remaining_en = re.findall(
        r"(<(?:p|h[2-6]|li|span)[^>]*>)"
        r"((?:(?!</).)*?[a-zA-Z]{4,}(?:(?!</).)*?)"
        r"(</(?:p|h[2-6]|li|span)>)",
        output,
    )
    for open_tag, inner, close_tag in remaining_en:
        en_chars = len(re.findall(r"[a-zA-Z]", inner))
        ru_chars = len(re.findall(r"[\u0400-\u04ff]", inner))
        # If more than 60% English, re-translate
        total = en_chars + ru_chars
        if total > 20 and en_chars / total > 0.6:
            translated = translate_text(inner)
            char_count += len(inner)
            output = output.replace(
                open_tag + inner + close_tag,
                open_tag + translated + close_tag,
                1,
            )

    DST.write_text(output, encoding="utf-8")

    usage = translator.get_usage()
    print(f"✓ Translated → {DST.relative_to(REPO_ROOT)}")
    print(f"  Characters sent: ~{char_count:,}")
    print(f"  DeepL usage: {usage.character.count:,} / {usage.character.limit:,}")


if __name__ == "__main__":
    main()
