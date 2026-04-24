import cloudscraper, re, time
from bs4 import BeautifulSoup

s = cloudscraper.create_scraper()
for tbl in ['winners-errors', 'pbp-stats', 'mcp-serve', 'mcp-return',
            'mcp-rally', 'serve-speed', 'pbp-points', 'pbp-games',
            'mcp-tactics']:
    time.sleep(3)
    r = s.get(
        f'https://www.tennisabstract.com/cgi-bin/player-more.cgi?'
        f'p=211663/Joao-Fonseca&table={tbl}'
    )
    m = re.search(r'var\s+player_frag\s*=\s*`(.*?)`', r.text, re.DOTALL)
    if not m:
        print(tbl, 'NO FRAG', 'bytes=', len(r.text))
        continue
    soup = BeautifulSoup(m.group(1), 'html.parser')
    t = soup.find('table', id=tbl) or soup.find('table')
    if not t:
        print(tbl, 'NO TABLE', 'bytes=', len(r.text))
        continue
    tbody = t.find('tbody')
    body_rows = (len(tbody.find_all('tr'))
                 if tbody else max(0, len(t.find_all('tr')) - 1))
    print(f'{tbl}: cgi={body_rows} rows, bytes={len(r.text)}')
