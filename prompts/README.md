# Valdo AI Prompt Library

Reusable AI prompts that convert batch file specification documents into Valdo-compatible CSV templates.

## What This Solves

Teams receive batch file specs as Excel/PDF/Word documents with natural language descriptions, COBOL picture clauses, and business rules embedded in free text. Manually translating these into Valdo's CSV format takes hours.

These prompts let any LLM (Copilot, GitLab Duo, Claude, ChatGPT, Gemini) do the translation in seconds.

## Workflow

```
1. Open your mapping spec (Excel, PDF, text)
2. Copy the content (or paste as a table)
3. Paste the appropriate prompt into your LLM tool
4. Paste the spec content after the prompt
5. LLM generates a CSV
6. Save as .csv and upload to Valdo UI (Mapping Generator tab)
7. Valdo converts to JSON — done
```

## Available Prompts

| Prompt | Input | Output | File |
|--------|-------|--------|------|
| **Mapping CSV** | Spec doc with fields, positions, types | `mapping_template.csv` for upload | [generate-mapping-csv.md](generate-mapping-csv.md) |
| **Rules CSV** | Spec doc with validation logic, required flags | `rules_template.csv` for upload | [generate-rules-csv.md](generate-rules-csv.md) |
| **Both** | Full spec doc | Both CSVs in one go | [generate-both.md](generate-both.md) |

## Using With Different LLM Tools

### GitHub Copilot Chat (VS Code)
1. Open your spec file in VS Code
2. Open Copilot Chat (`Ctrl+I` / `Cmd+I`)
3. Paste the prompt, then paste your spec content
4. Copy the CSV output and save

### GitLab Duo Chat
1. Open GitLab Duo Chat in your project
2. Paste the prompt followed by your spec content
3. Copy the generated CSV

### Claude / ChatGPT / Gemini
1. Start a new conversation
2. Upload or paste your spec document
3. Paste the prompt
4. Download or copy the CSV output

### Claude Code CLI
1. Paste the spec content into a file
2. Run: `cat spec.txt | claude "$(cat prompts/generate-mapping-csv.md)"`

## Examples

See [examples/](examples/) for tested input/output pairs from a real mapping specification.

## Tips

- **Large specs**: If your spec has 100+ fields, paste in batches of 20-30 fields per prompt
- **Multiple record types**: If your file has different record types (Header, Detail, Trailer), process each type separately
- **Review output**: Always review the generated CSV before uploading — AI may misinterpret ambiguous specs
- **Iterative refinement**: After the first generation, ask follow-up questions like "add cross-row rules for sequential numbering"
