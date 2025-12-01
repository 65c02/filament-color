#!/usr/bin/env python3
"""Script pour explorer la structure d'une page de filament"""

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import time

options = Options()
options.add_argument('--headless=new')
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')

print("Démarrage de Chrome...")
service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=service, options=options)

# Charger la bibliothèque
print("Chargement de la bibliothèque...")
driver.get("https://filamentcolors.xyz/library/")
time.sleep(3)

# Trouver un lien vers un swatch
soup = BeautifulSoup(driver.page_source, 'html.parser')
swatch_link = soup.select_one('a[href*="/swatch/"]')

if swatch_link:
    swatch_url = "https://filamentcolors.xyz" + swatch_link['href']
    print(f"\n=== URL du swatch: {swatch_url} ===\n")

    # Charger la page du swatch
    driver.get(swatch_url)
    time.sleep(2)

    # Analyser la page
    soup = BeautifulSoup(driver.page_source, 'html.parser')

    # Sauvegarder le HTML complet pour analyse
    with open("swatch_page.html", "w", encoding="utf-8") as f:
        f.write(soup.prettify())
    print("HTML sauvegardé dans swatch_page.html")

    # Afficher les éléments clés
    print("\n=== TITRE ===")
    h1 = soup.select_one('h1')
    if h1:
        print(h1.get_text(strip=True))

    print("\n=== DÉFINITIONS (dt/dd) ===")
    for dt in soup.select('dt'):
        dd = dt.find_next_sibling('dd')
        if dd:
            print(f"  {dt.get_text(strip=True)}: {dd.get_text(strip=True)}")

    print("\n=== TABLES ===")
    for table in soup.select('table'):
        for tr in table.select('tr'):
            cells = tr.select('th, td')
            if cells:
                print("  " + " | ".join(c.get_text(strip=True) for c in cells))

    print("\n=== ÉLÉMENTS AVEC STYLE BACKGROUND ===")
    for elem in soup.select('[style*="background"]')[:5]:
        print(f"  Tag: {elem.name}, Style: {elem.get('style', '')[:100]}")

    print("\n=== CLASSES INTÉRESSANTES ===")
    interesting = soup.select('.color, .swatch, .hex, .rgb, .manufacturer, .type, .temperature, .temp, [class*="color"], [class*="swatch"]')
    for elem in interesting[:10]:
        print(f"  {elem.name}.{elem.get('class')}: {elem.get_text(strip=True)[:50]}")

    print("\n=== LIENS ===")
    for a in soup.select('a[href]')[:10]:
        href = a.get('href', '')
        text = a.get_text(strip=True)[:30]
        if text:
            print(f"  {text}: {href}")

    print("\n=== SPANS/DIVS AVEC DONNÉES ===")
    for elem in soup.select('span, div'):
        text = elem.get_text(strip=True)
        if text and len(text) < 50 and any(x in text.lower() for x in ['#', 'rgb', '°', 'pla', 'abs', 'petg', 'temp']):
            print(f"  {elem.name}: {text}")

else:
    print("Aucun swatch trouvé sur la page")

driver.quit()
print("\nTerminé!")
