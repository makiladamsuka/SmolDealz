from duckduckgo_search import DDGS
# Instantly returns direct links without opening a browser!
results = DDGS().text("site:tronic.lk tof sensor", max_results=3)
print(results)