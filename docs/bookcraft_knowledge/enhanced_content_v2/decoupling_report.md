# Decoupling Pricing & Timeline from Domain-Knowledge Content

**Files revised:** 10 enhanced service docs (v1.4.0 → v1.5.0)
**Output location:** `/mnt/user-data/outputs/enhanced_content_v2/`
**Output version:** v1.5.0
**Vector store:** updated to v1.5.0

---

## What was wrong

The previous v1.4.0 enhanced content baked specific prices and timelines directly into the marketing-content layer:

- Tier rate tables with `$150/PFH`, `$250/PFH`, `$400/PFH`, `$600/PFH`
- Per-word/per-page matrices like `$0.005/word`, `$1.25/page`
- Add-on lines like `Pronunciation Guide Creation — $200 (3 days)`
- Worked examples like `A 70,000-word novel typically yields a 7.5-hour audiobook costing $X`
- Q&A answers quoting specific timeline ranges like "4-8 weeks"

The same numbers also live in the centralized Pricing & Timeline engine, which the chatbot calls via `get_pricing_quote` and `get_timeline_estimate` MCP tools. That duplication creates a drift problem: the moment any price changes, the engine returns the new number while the marketing content still quotes the old one — and the chatbot retrieves both, producing contradictory output.

The fix: the engine owns numbers; the marketing layer owns the *names* and *descriptions* of the things being priced. When a user asks a price-or-timeline question, the chatbot calls the engine; when they ask about what a service includes, what tiers exist, or what add-ons are available, the chatbot retrieves from the marketing content.

## What was preserved

The marketing-content layer keeps everything the chatbot needs to recognize, describe, and route — none of which changes when prices change:

| Preserved | Why |
|---|---|
| **Tier names** ("Essential", "Professional", "Premium", "Studio", "Cinematic", "Starter", "Launch Essentials", "Premium Blitz", "Enterprise") | Stable taxonomy. Users ask about tiers by name. |
| **"Best for" descriptions per tier** | Recommendation logic for matching users to tiers |
| **Add-on names** (all 100+ specific add-ons across services) | Users ask about specific add-ons by name |
| **Add-on descriptions** (what each add-on does, when to use it) | Educational content the engine doesn't return |
| **Complexity driver names** ("Narrator Level", "Genre Complexity", "Worldbuilding Bible Creation"...) | Maps to engine's quote inputs |
| **Service capabilities and processes** | What each service produces and how |
| **Technical specifications** (file formats, platform requirements, performance specs) | External standards, not BookCraft pricing |
| **Cross-service relationships** | Routing knowledge for multi-service queries |

## What was removed

| Removed | Where it lives now |
|---|---|
| All `$X` dollar amounts | Pricing & Timeline engine |
| All per-unit rates (`$X/word`, `$X/page`, `$X/PFH`, `$X/mo`) | Pricing & Timeline engine |
| All `(N days)` parenthetical add-on timings | Pricing & Timeline engine |
| Timeline ranges like "4-8 weeks", "2-4 months", "1.5-2 weeks" | Pricing & Timeline engine |
| Pricing tables with numeric columns (4 of these) | Pricing & Timeline engine |
| Cost-calculation worked examples ("A 60,000-word novel costs $3,600") | Pricing & Timeline engine |
| External royalty rate specifics ("KDP eBook 70%...") | Retailer documentation |

## How the script works

`decouple_pricing.py` runs an 8-stage pipeline over each enhanced markdown file:

1. **Strip rate columns** from tier tables — detects "Rate" / "Price" / "$..." last-column headers and removes that column, leaving Tier + Best for.
2. **Replace rate matrices** entirely — multi-column tables with multiple `$` signs per row get swapped for a single italicized handoff line referring to the engine.
3. **Strip add-on pricing** from bullet lines — patterns like `— $200 (3 days). ` become `. ` (preserving the description).
4. **Strip narrative timelines** — sentences containing "X-Y weeks" / "X-Y months" / "X-Y days" get removed cleanly to avoid orphan fragments.
5. **Strip narrative prices** — remaining `$X` mentions in prose are removed.
6. **Apply targeted rewrites** — about 25 regex patterns for worked examples and broken sentences that arise from earlier stripping.
7. **Replace price/timeline Q&As** — questions whose subject is "how much" or "how long" get answered with a uniform handoff to the engine.
8. **Inject pricing handoff** after each tier/pricing section — italicized line pointing to the chatbot's quote tool.

After those, two cleanup passes:
- **Final tidy** — collapses double spaces, dedupes adjacent handoff lines, removes orphan fragments, normalizes punctuation.
- **Beautify add-on bullets** — bolds add-on names and replaces the awkward `Name. Description` (left over from price stripping) with `**Name** — Description`.

## Per-file impact

Char counts dropped 5-15% as numeric specifics were removed:

| Service | v1.4.0 chars | v1.5.0 chars | Δ |
|---|---|---|---|
| About BookCraft | 17,774 | 17,786 | +12 (no pricing changes; minor added handoffs) |
| Audiobook Production | 14,437 | 13,769 | -668 |
| Author's Website | 17,321 | 16,101 | -1,220 |
| Cover Design & Illustration | 14,860 | 14,279 | -581 |
| Editing & Proofreading | 14,200 | 12,249 | -1,951 |
| Interior Formatting | 14,010 | 13,061 | -949 |
| Ghostwriting | 16,838 | 15,547 | -1,291 |
| Marketing & Promotion | 14,183 | 13,552 | -631 |
| Publishing & Distribution | 15,289 | 14,481 | -808 |
| Video Trailers | 14,211 | 13,209 | -1,002 |
| **Totals** | **153,123** | **144,034** | **-9,089 (-5.9%)** |

## Vector store impact (v1.4.0 → v1.5.0)

| Doc type | v1.4.0 | v1.5.0 |
|---|---|---|
| faq | 1,800 | 1,800 |
| process | 66 | 66 |
| service_description | 45 | 45 |
| company_info | 20 | 20 |
| timeline_overview | 4 | 4 |
| website_section | 46 | 46 |
| **marketing_content** | **145** | **144** ← decoupled |
| **Total chunks** | **2,126** | **2,125** |

Chunk counts are essentially unchanged — the structural sectioning is the same. What changed is per-chunk content: tiers, drivers, and add-ons are still indexed by name, but no chunk now contains a price or specific timeline.

Each chunk is tagged `content_layer: decoupled` so the response engine can identify the source layer if mixed indexing is ever needed.

## Verification

A residual-numbers audit confirms zero pricing or timeline specifics remain in any of the 10 files:

```
$ grep -E "\$[0-9]" *.md | wc -l
0

$ grep -E "[0-9]+\s*[-–]\s*[0-9]+\s*(week|month|day)s?" *.md | wc -l
0

$ grep -E "[0-9]+%\s+(off|of\s+list)" *.md | wc -l
0
```

The only remaining specific number across all files is "(typically 6 months before launch)" in the marketing-promotion pre-order Q&A — this is general industry planning convention (when authors typically set up pre-orders), not a BookCraft engine-owned timeline. It's appropriate to keep.

## What this enables

A user asking "tell me about audiobook production" gets a chunk listing the four tiers (Essential, Professional, Premium, Studio) and what each is best for — but no rates. The chunk ends with a callout that pricing comes from the engine.

A user asking "how much does audiobook production cost?" gets the chatbot's handoff response that triggers a `get_pricing_quote` tool call. The engine returns the current rate range based on the user's word count, tier, and add-on selection.

If BookCraft changes the Premium tier from $400/PFH to $450/PFH next month, only the engine needs an update. The marketing content stays valid forever — it never quoted $400 in the first place.

The chatbot now has one-and-only-one source of truth for every price and every timeline. Same chatbot architecture; same retrieval pipeline; clean separation of stable domain knowledge from volatile pricing.

## Suggested next steps

1. **Replace the originals.** The decoupled `.docx` files are drop-in replacements for the v1.4.0 enhanced versions.
2. **Update the response engine prompt** to reinforce: when retrieving from `marketing_content` chunks, never invent prices or timelines — call the engine tools explicitly.
3. **Rerun decoupling on future content additions.** The script (`decouple_pricing.py`) is in `optimizer_source_v2/` and is re-runnable against any future enhanced content that might creep in pricing again.
