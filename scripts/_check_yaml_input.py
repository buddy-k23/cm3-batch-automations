"""Show the raw HTML around the qtMrYamlInput element."""
content = open("src/reports/static/ui.html", encoding="utf-8").read()
idx = content.index("qtMrYamlInput")
print(repr(content[max(0, idx-400):idx+300]))
