import trafilatura
from ddgs import DDGS # pyright: ignore[reportUnknownVariableType]

class Web:
    def __init__(self):
        self.tools = {f.__name__: f for f in (self.search_web, self.read_page)}
    
    def search_web(self, query: str) -> str:
        """Search the web for current information, news, or facts you don't know.

        Args:
            query: A short search query, like 'rtx 5070 price' or 'weather in Arcata'.
        """
        try:
            results = DDGS().text(query, max_results=5)
            if not results:
                return "No results found."
            
            return "\n\n".join(
                f"{r['title']}\n{r['href']}\n{r['body'][:300]}" for r in results
            )
        except Exception as err:
            return f"Error: {err}"
    
    def read_page(self, url: str) -> str:
        """Read the main text of a web page, like an article from the search results.

        Args:
            url: The full address of the page, starting with http:// or https://.
        """
        
        try:
            html = trafilatura.fetch_url(url)
            if not html:
                return f"Could not load {url}."
            
            text = trafilatura.extract(html)
            if not text:
                return "Could not find readable text on that page."
            
            return text
        except Exception as err:
            return f"Error: {err}"