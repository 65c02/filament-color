#!/usr/bin/env python3
"""
Filament Colors Scraper & Viewer
Scrape https://filamentcolors.xyz/library/ et stocke dans SQLite
Viewer PyQt5 pour afficher les fiches
"""

import sys
import sqlite3
import time
import re
import json
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QHeaderView, QPushButton,
    QLabel, QLineEdit, QComboBox, QProgressBar, QMessageBox,
    QSplitter, QFrame, QScrollArea, QGridLayout, QGroupBox,
    QStatusBar, QTextEdit, QCheckBox, QMenuBar, QMenu, QAction,
    QFileDialog
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QFont

# Configuration
BASE_URL = "https://filamentcolors.xyz"
LIBRARY_URL = f"{BASE_URL}/library/"
DB_PATH = Path(__file__).parent / "filaments.db"
PROGRESS_FILE = Path(__file__).parent / "scrape_progress.json"


def init_database():
    """Initialise la base de données SQLite"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS filaments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE,
            name TEXT,
            manufacturer TEXT,
            color_name TEXT,
            material_type TEXT,
            hex_color TEXT,
            complement_hex TEXT,
            rgb_r INTEGER,
            rgb_g INTEGER,
            rgb_b INTEGER,
            hsl_h REAL,
            hsl_s REAL,
            hsl_l REAL,
            temperature_bed TEXT,
            temperature_hotend TEXT,
            is_transparent INTEGER DEFAULT 0,
            is_glitter INTEGER DEFAULT 0,
            is_glow INTEGER DEFAULT 0,
            notes TEXT,
            image_url TEXT,
            date_added TEXT,
            td_hex TEXT,
            amazon_link TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filament_id INTEGER,
            tag TEXT,
            FOREIGN KEY (filament_id) REFERENCES filaments(id)
        )
    ''')

    conn.commit()
    return conn


class SeleniumScraperThread(QThread):
    """Thread pour le scraping avec Selenium"""
    progress = pyqtSignal(int, int, str)
    finished_signal = pyqtSignal(int)
    error = pyqtSignal(str)
    paused = pyqtSignal(int, int)  # current_index, total
    filament_added = pyqtSignal(dict)  # données du filament ajouté/mis à jour

    def __init__(self, resume_data=None, full_update=False, db_path=None):
        super().__init__()
        self.running = True
        self.paused_flag = False
        self.driver = None
        self.resume_data = resume_data  # {"urls": [...], "index": N}
        self.full_update = full_update
        self.db_path = db_path or DB_PATH
        self.current_urls = []
        self.current_index = 0

    def stop(self):
        self.running = False
        if self.driver:
            try:
                self.driver.quit()
            except:
                pass

    def pause(self):
        """Met en pause et sauvegarde l'état"""
        self.paused_flag = True
        self.running = False

    def save_progress(self):
        """Sauvegarde l'état actuel dans un fichier"""
        if self.current_urls:
            data = {
                "urls": self.current_urls,
                "index": self.current_index,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f)
            return True
        return False

    @staticmethod
    def load_progress():
        """Charge l'état sauvegardé"""
        if PROGRESS_FILE.exists():
            try:
                with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass
        return None

    @staticmethod
    def clear_progress():
        """Supprime le fichier de progression"""
        if PROGRESS_FILE.exists():
            PROGRESS_FILE.unlink()

    def format_duration(self, seconds):
        """Formate une durée en heures/minutes/secondes"""
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            mins = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{mins}m {secs}s"
        else:
            hours = int(seconds // 3600)
            mins = int((seconds % 3600) // 60)
            return f"{hours}h {mins}m"

    def is_entry_complete(self, conn, url):
        """Vérifie si une entrée existe et a tous ses champs principaux remplis"""
        cursor = conn.cursor()
        cursor.execute('''
            SELECT name, manufacturer, hex_color, material_type,
                   temperature_hotend, temperature_bed
            FROM filaments WHERE url = ?
        ''', (url,))
        row = cursor.fetchone()

        if not row:
            return False  # N'existe pas

        # Vérifier que les champs principaux sont remplis
        name, manufacturer, hex_color, material_type, temp_hotend, temp_bed = row
        if name and manufacturer and hex_color:
            return True  # Considéré comme complet

        return False

    def setup_driver(self):
        """Configure le driver Selenium"""
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.chrome.options import Options
        from webdriver_manager.chrome import ChromeDriverManager

        options = Options()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=options)
        self.driver.implicitly_wait(10)

    def run(self):
        try:
            self.progress.emit(0, 0, "Initialisation de Selenium...")
            self.setup_driver()

            conn = sqlite3.connect(str(self.db_path))

            # Vérifier si on reprend depuis une sauvegarde
            start_index = 0
            if self.resume_data:
                self.progress.emit(0, 0, "Reprise depuis la sauvegarde...")
                urls_to_scrape = self.resume_data["urls"]
                start_index = self.resume_data["index"]
                self.progress.emit(start_index, len(urls_to_scrape),
                                 f"Reprise à partir de {start_index}/{len(urls_to_scrape)}")
            else:
                # Récupérer la liste des filaments
                self.progress.emit(0, 0, "Récupération de la liste des filaments...")
                filament_urls = self.get_filament_list()

                if not filament_urls:
                    self.error.emit("Impossible de récupérer la liste des filaments. Vérifiez que Chrome est installé.")
                    return

                total = len(filament_urls)
                self.progress.emit(0, total, f"Trouvé {total} filaments à scraper")

                # On scrape tous les URLs (ajout ou mise à jour)
                urls_to_scrape = filament_urls

            # Sauvegarder pour la reprise potentielle
            self.current_urls = urls_to_scrape

            count = 0
            skipped = 0
            start_time = time.time()

            for i in range(start_index, len(urls_to_scrape)):
                if not self.running:
                    break

                url = urls_to_scrape[i]
                self.current_index = i

                # Vérifier si on peut sauter cette entrée (sauf si full_update)
                if not self.full_update and self.is_entry_complete(conn, url):
                    skipped += 1
                    self.progress.emit(i + 1, len(urls_to_scrape),
                                     f"[{i+1}/{len(urls_to_scrape)}] Ignoré (complet) - {skipped} ignorés")
                    continue

                try:
                    filament_data = self.scrape_filament_page(url)
                    if filament_data:
                        filament_id = self.save_filament(conn, filament_data, self.full_update)
                        filament_data['id'] = filament_id
                        self.filament_added.emit(filament_data)  # Notifier le viewer
                        count += 1
                        name = filament_data.get('name', 'N/A')
                    else:
                        name = 'erreur'
                except Exception as e:
                    print(f"Erreur sur {url}: {e}")
                    name = 'erreur'

                # Calcul du temps restant estimé
                elapsed = time.time() - start_time
                items_done = count + 1  # Seulement les items traités (pas ignorés)
                items_remaining = len(urls_to_scrape) - i - 1 - skipped
                if items_done > 0 and elapsed > 0:
                    avg_time = elapsed / items_done
                    remaining = avg_time * max(0, items_remaining)
                    eta = self.format_duration(remaining)
                else:
                    eta = "calcul..."

                status = f"[{i+1}/{len(urls_to_scrape)}] {name}"
                if skipped > 0:
                    status += f" ({skipped} ignorés)"
                status += f" - Restant: {eta}"
                self.progress.emit(i + 1, len(urls_to_scrape), status)
                time.sleep(0.5)  # Rate limiting

            conn.close()
            if self.driver:
                self.driver.quit()

            # Gestion de la pause
            if self.paused_flag:
                self.save_progress()
                self.paused.emit(self.current_index, len(urls_to_scrape))
            else:
                # Scraping terminé, supprimer le fichier de progression
                self.clear_progress()
                self.finished_signal.emit(count)

        except Exception as e:
            import traceback
            self.error.emit(f"{str(e)}\n{traceback.format_exc()}")
            if self.driver:
                try:
                    self.driver.quit()
                except:
                    pass

    def get_filament_list(self):
        """Récupère la liste de tous les URLs de filaments avec infinite scroll"""
        urls = set()
        target_count = 3056  # Nombre attendu de filaments

        self.driver.get(LIBRARY_URL)
        time.sleep(3)  # Attendre le chargement initial

        last_count = 0
        no_change_count = 0
        max_no_change = 5  # Arrêter après 5 scrolls sans nouveaux éléments
        start_time = time.time()
        scroll_count = 0

        while self.running and no_change_count < max_no_change:
            try:
                # Récupérer les liens actuels
                soup = BeautifulSoup(self.driver.page_source, 'html.parser')

                # Chercher les liens vers les swatches
                swatch_links = soup.select('a[href*="/swatch/"]')

                if not swatch_links:
                    all_links = soup.find_all('a', href=True)
                    swatch_links = [a for a in all_links if '/swatch/' in a['href']]

                # Extraire les URLs
                for link in swatch_links:
                    href = link.get('href', '')
                    if href:
                        full_url = urljoin(BASE_URL, href)
                        if '/swatch/' in full_url:
                            urls.add(full_url)

                current_count = len(urls)
                scroll_count += 1

                # Calcul du temps restant estimé
                elapsed = time.time() - start_time
                if current_count > 0 and elapsed > 0:
                    # Vitesse: combien d'URLs par seconde
                    rate = current_count / elapsed
                    if rate > 0:
                        remaining_count = max(0, target_count - current_count)
                        remaining_time = remaining_count / rate
                        eta = self.format_duration(remaining_time)
                    else:
                        eta = "calcul..."
                else:
                    eta = "calcul..."

                progress_pct = min(100, int(current_count / target_count * 100))
                self.progress.emit(current_count, target_count,
                                 f"Chargement liste: {current_count}/{target_count} ({progress_pct}%) - Restant: {eta}")

                # Vérifier si on a trouvé de nouveaux éléments
                if current_count == last_count:
                    no_change_count += 1
                else:
                    no_change_count = 0
                    last_count = current_count

                # Scroller vers le bas de la page
                self.driver.execute_script("""
                    window.scrollTo(0, document.body.scrollHeight);
                """)
                time.sleep(1.5)  # Attendre le chargement du nouveau contenu

                # Aussi essayer de cliquer sur un bouton "Load More" s'il existe
                try:
                    load_more = self.driver.find_elements("css selector",
                        "button.load-more, a.load-more, [data-load-more], button:contains('Load'), button:contains('More')")
                    for btn in load_more:
                        if btn.is_displayed():
                            btn.click()
                            time.sleep(2)
                            break
                except:
                    pass

            except Exception as e:
                print(f"Erreur scroll: {e}")
                no_change_count += 1

        print(f"Total URLs collectés: {len(urls)}")
        return list(urls)

    def scrape_filament_page(self, url):
        """Scrape une page de filament individuelle"""
        try:
            self.driver.get(url)
            time.sleep(1)

            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            data = {'url': url}

            # Nom du filament (h1 ou titre)
            title = soup.select_one('h1')
            if title:
                data['name'] = title.get_text(strip=True)

            # Fallback: titre de la page
            if not data.get('name'):
                page_title = soup.select_one('title')
                if page_title:
                    # Nettoyer le titre (souvent "Nom - Site")
                    title_text = page_title.get_text(strip=True)
                    if ' - ' in title_text:
                        data['name'] = title_text.split(' - ')[0].strip()
                    elif ' | ' in title_text:
                        data['name'] = title_text.split(' | ')[0].strip()
                    else:
                        data['name'] = title_text

            # Chercher les informations dans les définitions (dl/dt/dd)
            for dt in soup.select('dt'):
                label = dt.get_text(strip=True).lower()
                dd = dt.find_next_sibling('dd')
                if not dd:
                    continue
                value = dd.get_text(strip=True)

                if 'manufacturer' in label or 'fabricant' in label or 'brand' in label:
                    data['manufacturer'] = value
                elif 'filament type' in label or 'material type' in label or label == 'type':
                    data['material_type'] = value
                elif ('color' in label or 'colour' in label) and 'hex' not in label and 'complement' not in label:
                    data['color_name'] = value
                elif 'bed' in label and 'temp' in label:
                    data['temperature_bed'] = value
                elif 'hot end' in label or 'hotend' in label or 'nozzle' in label or 'extruder' in label:
                    data['temperature_hotend'] = value

            # Chercher aussi dans les tableaux (th/td)
            for tr in soup.select('tr'):
                cells = tr.select('th, td')
                if len(cells) >= 2:
                    label = cells[0].get_text(strip=True).lower()
                    value = cells[1].get_text(strip=True)

                    if ('manufacturer' in label or 'brand' in label) and 'manufacturer' not in data:
                        data['manufacturer'] = value
                    elif ('type' in label or 'material' in label) and 'material_type' not in data:
                        data['material_type'] = value
                    elif ('color' in label and 'hex' not in label) and 'color_name' not in data:
                        data['color_name'] = value
                    elif 'bed' in label and 'temperature_bed' not in data:
                        data['temperature_bed'] = value
                    elif ('hotend' in label or 'nozzle' in label or 'hot end' in label) and 'temperature_hotend' not in data:
                        data['temperature_hotend'] = value

            # Chercher dans les spans/divs avec classes spécifiques
            for elem in soup.select('[class*="manufacturer"], [class*="brand"]'):
                if 'manufacturer' not in data:
                    data['manufacturer'] = elem.get_text(strip=True)
            for elem in soup.select('[class*="material"], [class*="type"]'):
                text = elem.get_text(strip=True)
                if 'material_type' not in data and text and len(text) < 50:
                    data['material_type'] = text
            for elem in soup.select('[class*="color-name"], [class*="colour"]'):
                if 'color_name' not in data:
                    data['color_name'] = elem.get_text(strip=True)

            # Couleur HEX - chercher dans le style ou le texte
            # Souvent dans un div avec style background-color
            color_elems = soup.select('[style*="background"]')
            for elem in color_elems:
                style = elem.get('style', '')
                hex_match = re.search(r'#([0-9a-fA-F]{6})', style)
                if hex_match:
                    data['hex_color'] = f"#{hex_match.group(1)}"
                    break

            # Chercher hex dans le texte de la page
            if 'hex_color' not in data:
                page_text = soup.get_text()
                hex_matches = re.findall(r'#([0-9a-fA-F]{6})\b', page_text)
                if hex_matches:
                    data['hex_color'] = f"#{hex_matches[0]}"

            # Chercher dans les balises spécifiques au site
            hex_elem = soup.select_one('[data-hex], .hex-value, .color-hex')
            if hex_elem:
                hex_text = hex_elem.get('data-hex') or hex_elem.get_text(strip=True)
                if hex_text and re.match(r'^#?[0-9a-fA-F]{6}$', hex_text):
                    data['hex_color'] = hex_text if hex_text.startswith('#') else f"#{hex_text}"

            # Convertir hex en RGB
            if 'hex_color' in data:
                hex_val = data['hex_color'].lstrip('#')
                if len(hex_val) == 6:
                    data['rgb_r'] = int(hex_val[0:2], 16)
                    data['rgb_g'] = int(hex_val[2:4], 16)
                    data['rgb_b'] = int(hex_val[4:6], 16)

            # TD (Transmittance Distance) - chercher dans dt/dd et autres éléments
            for dt in soup.select('dt'):
                label = dt.get_text(strip=True).lower()
                if 'td' in label or 'transmittance' in label:
                    dd = dt.find_next_sibling('dd')
                    if dd:
                        td_text = dd.get_text(strip=True)
                        # Chercher une couleur hex dans le texte
                        td_match = re.search(r'#?([0-9a-fA-F]{6})', td_text)
                        if td_match:
                            data['td_hex'] = f"#{td_match.group(1)}"
                        else:
                            data['td_hex'] = td_text

            # Chercher TD dans les tableaux
            for tr in soup.select('tr'):
                cells = tr.select('th, td')
                if len(cells) >= 2:
                    label = cells[0].get_text(strip=True).lower()
                    if ('td' in label or 'transmittance' in label) and 'td_hex' not in data:
                        td_text = cells[1].get_text(strip=True)
                        td_match = re.search(r'#?([0-9a-fA-F]{6})', td_text)
                        if td_match:
                            data['td_hex'] = f"#{td_match.group(1)}"
                        else:
                            data['td_hex'] = td_text

            # Chercher TD dans les éléments avec classe spécifique
            for elem in soup.select('[class*="td"], [class*="transmittance"]'):
                if 'td_hex' not in data:
                    td_text = elem.get_text(strip=True)
                    td_match = re.search(r'#?([0-9a-fA-F]{6})', td_text)
                    if td_match:
                        data['td_hex'] = f"#{td_match.group(1)}"

            # Propriétés spéciales
            page_text_lower = soup.get_text().lower()
            if 'transparent' in page_text_lower or 'translucent' in page_text_lower:
                data['is_transparent'] = 1
            if 'glitter' in page_text_lower or 'sparkle' in page_text_lower:
                data['is_glitter'] = 1
            if 'glow' in page_text_lower or 'phosphorescent' in page_text_lower:
                data['is_glow'] = 1

            # Image
            img = soup.select_one('img.swatch, img[alt*="swatch"], main img, .swatch-image img')
            if img:
                img_src = img.get('src', '')
                if img_src:
                    data['image_url'] = urljoin(BASE_URL, img_src)

            # Tags
            tags = []
            for tag_elem in soup.select('.tag, .badge, .chip, .label'):
                tag_text = tag_elem.get_text(strip=True)
                if tag_text:
                    tags.append(tag_text.lower())
            data['tags'] = tags

            # Notes/description
            notes_elem = soup.select_one('.notes, .description, .comment, p.info')
            if notes_elem:
                data['notes'] = notes_elem.get_text(strip=True)

            # Dernier fallback pour le nom: construire depuis fabricant + couleur
            if not data.get('name'):
                parts = []
                if data.get('manufacturer'):
                    parts.append(data['manufacturer'])
                if data.get('color_name'):
                    parts.append(data['color_name'])
                if parts:
                    data['name'] = ' - '.join(parts)

            return data

        except Exception as e:
            print(f"Erreur scraping {url}: {e}")
            return None

    def save_filament(self, conn, data, full_update=False):
        """Sauvegarde un filament dans la base de données (insert ou update partiel)"""
        cursor = conn.cursor()

        try:
            # Vérifier si l'URL existe déjà
            cursor.execute('SELECT * FROM filaments WHERE url = ?', (data.get('url'),))
            existing = cursor.fetchone()

            if existing:
                filament_id = existing[0]

                if full_update:
                    # Mise à jour complète
                    cursor.execute('''
                        UPDATE filaments SET
                            name = ?, manufacturer = ?, color_name = ?, material_type = ?,
                            hex_color = ?, rgb_r = ?, rgb_g = ?, rgb_b = ?,
                            temperature_bed = ?, temperature_hotend = ?,
                            is_transparent = ?, is_glitter = ?, is_glow = ?,
                            notes = ?, image_url = ?, td_hex = ?
                        WHERE id = ?
                    ''', (
                        data.get('name'),
                        data.get('manufacturer'),
                        data.get('color_name'),
                        data.get('material_type'),
                        data.get('hex_color'),
                        data.get('rgb_r'),
                        data.get('rgb_g'),
                        data.get('rgb_b'),
                        data.get('temperature_bed'),
                        data.get('temperature_hotend'),
                        data.get('is_transparent', 0),
                        data.get('is_glitter', 0),
                        data.get('is_glow', 0),
                        data.get('notes'),
                        data.get('image_url'),
                        data.get('td_hex'),
                        filament_id
                    ))
                else:
                    # Mise à jour partielle - seulement les champs manquants
                    # Récupérer les noms de colonnes
                    col_names = [desc[0] for desc in cursor.description]
                    existing_data = dict(zip(col_names, existing))

                    # Champs à potentiellement mettre à jour
                    fields_to_update = []
                    values = []

                    field_mapping = {
                        'name': 'name',
                        'manufacturer': 'manufacturer',
                        'color_name': 'color_name',
                        'material_type': 'material_type',
                        'hex_color': 'hex_color',
                        'rgb_r': 'rgb_r',
                        'rgb_g': 'rgb_g',
                        'rgb_b': 'rgb_b',
                        'temperature_bed': 'temperature_bed',
                        'temperature_hotend': 'temperature_hotend',
                        'is_transparent': 'is_transparent',
                        'is_glitter': 'is_glitter',
                        'is_glow': 'is_glow',
                        'notes': 'notes',
                        'image_url': 'image_url',
                        'td_hex': 'td_hex'
                    }

                    for db_field, data_field in field_mapping.items():
                        existing_val = existing_data.get(db_field)
                        new_val = data.get(data_field)

                        # Mettre à jour si le champ existant est vide et le nouveau non
                        if (existing_val is None or existing_val == '' or existing_val == 0) and new_val:
                            fields_to_update.append(f"{db_field} = ?")
                            values.append(new_val)
                        elif existing_val and not new_val:
                            # Garder la valeur existante dans data pour l'affichage
                            data[data_field] = existing_val

                    if fields_to_update:
                        values.append(filament_id)
                        query = f"UPDATE filaments SET {', '.join(fields_to_update)} WHERE id = ?"
                        cursor.execute(query, values)
            else:
                # Insertion
                cursor.execute('''
                    INSERT INTO filaments
                    (url, name, manufacturer, color_name, material_type, hex_color,
                     rgb_r, rgb_g, rgb_b, temperature_bed, temperature_hotend,
                     is_transparent, is_glitter, is_glow, notes, image_url, td_hex)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    data.get('url'),
                    data.get('name'),
                    data.get('manufacturer'),
                    data.get('color_name'),
                    data.get('material_type'),
                    data.get('hex_color'),
                    data.get('rgb_r'),
                    data.get('rgb_g'),
                    data.get('rgb_b'),
                    data.get('temperature_bed'),
                    data.get('temperature_hotend'),
                    data.get('is_transparent', 0),
                    data.get('is_glitter', 0),
                    data.get('is_glow', 0),
                    data.get('notes'),
                    data.get('image_url'),
                    data.get('td_hex')
                ))
                filament_id = cursor.lastrowid

            # Supprimer les anciens tags
            cursor.execute('DELETE FROM tags WHERE filament_id = ?', (filament_id,))

            # Sauvegarder les nouveaux tags
            for tag in data.get('tags', []):
                cursor.execute(
                    'INSERT INTO tags (filament_id, tag) VALUES (?, ?)',
                    (filament_id, tag)
                )

            conn.commit()
            return filament_id

        except Exception as e:
            print(f"Erreur sauvegarde: {e}")
            return None


class ColorTableItem(QTableWidgetItem):
    """Item de table personnalisé pour le tri par couleur"""
    def __lt__(self, other):
        # Trier par la donnée UserRole (HSL)
        self_data = self.data(Qt.UserRole) or "999999999"
        other_data = other.data(Qt.UserRole) or "999999999"
        return self_data < other_data


class ColorSwatch(QFrame):
    """Widget pour afficher un échantillon de couleur"""
    def __init__(self, hex_color=None, parent=None):
        super().__init__(parent)
        self.setFixedSize(80, 80)
        self.setFrameStyle(QFrame.Box | QFrame.Raised)
        self.set_color(hex_color)

    def set_color(self, hex_color):
        if hex_color:
            self.setStyleSheet(f"""
                QFrame {{
                    background-color: {hex_color};
                    border: 2px solid #333;
                    border-radius: 5px;
                }}
            """)
        else:
            self.setStyleSheet("""
                QFrame {
                    background-color: #cccccc;
                    border: 2px solid #333;
                    border-radius: 5px;
                }
            """)


class FilamentDetailWidget(QScrollArea):
    """Widget pour afficher les détails d'un filament"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)

        container = QWidget()
        layout = QVBoxLayout(container)

        # Header avec couleur
        header = QHBoxLayout()
        self.color_swatch = ColorSwatch()
        header.addWidget(self.color_swatch)

        title_layout = QVBoxLayout()
        self.name_label = QLabel("Sélectionnez un filament")
        self.name_label.setFont(QFont('Arial', 16, QFont.Bold))
        self.name_label.setWordWrap(True)
        self.manufacturer_label = QLabel("")
        self.manufacturer_label.setFont(QFont('Arial', 12))
        title_layout.addWidget(self.name_label)
        title_layout.addWidget(self.manufacturer_label)
        title_layout.addStretch()

        header.addLayout(title_layout)
        header.addStretch()
        layout.addLayout(header)

        # Informations
        info_group = QGroupBox("Informations")
        info_layout = QGridLayout(info_group)

        self.info_labels = {}
        fields = [
            ('material_type', 'Type de matériau'),
            ('hex_color', 'Couleur HEX'),
            ('rgb', 'Couleur RGB'),
            ('td_hex', 'TD (Transmittance)'),
            ('temperature_hotend', 'Temp. Hotend'),
            ('temperature_bed', 'Temp. Lit'),
        ]

        for i, (key, label) in enumerate(fields):
            lbl = QLabel(f"{label}:")
            lbl.setFont(QFont('Arial', 10, QFont.Bold))
            val = QLabel("-")
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            info_layout.addWidget(lbl, i, 0)
            info_layout.addWidget(val, i, 1)
            self.info_labels[key] = val

        layout.addWidget(info_group)

        # Propriétés spéciales
        props_group = QGroupBox("Propriétés")
        props_layout = QHBoxLayout(props_group)

        self.transparent_label = QLabel("Transparent")
        self.glitter_label = QLabel("Paillettes")
        self.glow_label = QLabel("Phosphorescent")

        for lbl in [self.transparent_label, self.glitter_label, self.glow_label]:
            lbl.setStyleSheet("padding: 5px; border-radius: 3px;")
            props_layout.addWidget(lbl)
        props_layout.addStretch()

        layout.addWidget(props_group)

        # Notes
        notes_group = QGroupBox("Notes")
        notes_layout = QVBoxLayout(notes_group)
        self.notes_text = QTextEdit()
        self.notes_text.setReadOnly(True)
        self.notes_text.setMaximumHeight(100)
        notes_layout.addWidget(self.notes_text)
        layout.addWidget(notes_group)

        # URL
        url_layout = QHBoxLayout()
        url_layout.addWidget(QLabel("URL:"))
        self.url_label = QLabel("")
        self.url_label.setOpenExternalLinks(True)
        self.url_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.url_label.setWordWrap(True)
        url_layout.addWidget(self.url_label, 1)
        layout.addLayout(url_layout)

        layout.addStretch()
        self.setWidget(container)

    def display_filament(self, data):
        """Affiche les données d'un filament"""
        if not data:
            return

        self.name_label.setText(data.get('name') or 'N/A')
        self.manufacturer_label.setText(data.get('manufacturer') or 'N/A')

        hex_color = data.get('hex_color')
        self.color_swatch.set_color(hex_color)

        self.info_labels['material_type'].setText(data.get('material_type') or '-')
        self.info_labels['hex_color'].setText(hex_color or '-')

        if data.get('rgb_r') is not None:
            rgb = f"({data.get('rgb_r')}, {data.get('rgb_g')}, {data.get('rgb_b')})"
        else:
            rgb = '-'
        self.info_labels['rgb'].setText(rgb)

        self.info_labels['td_hex'].setText(data.get('td_hex') or '-')
        self.info_labels['temperature_hotend'].setText(data.get('temperature_hotend') or '-')
        self.info_labels['temperature_bed'].setText(data.get('temperature_bed') or '-')

        # Propriétés
        self.transparent_label.setStyleSheet(
            "padding: 5px; border-radius: 3px; background-color: #90EE90;"
            if data.get('is_transparent') else
            "padding: 5px; border-radius: 3px; background-color: #ddd; color: #999;"
        )
        self.glitter_label.setStyleSheet(
            "padding: 5px; border-radius: 3px; background-color: #FFD700;"
            if data.get('is_glitter') else
            "padding: 5px; border-radius: 3px; background-color: #ddd; color: #999;"
        )
        self.glow_label.setStyleSheet(
            "padding: 5px; border-radius: 3px; background-color: #00FF00;"
            if data.get('is_glow') else
            "padding: 5px; border-radius: 3px; background-color: #ddd; color: #999;"
        )

        self.notes_text.setText(data.get('notes') or '')

        url = data.get('url', '')
        if url:
            self.url_label.setText(f'<a href="{url}">{url}</a>')


class MainWindow(QMainWindow):
    """Fenêtre principale de l'application"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Filament Colors Viewer")
        self.setMinimumSize(1400, 800)
        self.resize(1600, 900)

        self.scraper_thread = None
        self.saved_progress = None  # Stocke les données de reprise
        self.current_db_path = DB_PATH  # Base de données actuelle
        self.setup_menu()
        self.setup_ui()
        self.load_data()
        self.check_saved_progress()

    def setup_menu(self):
        """Configure la barre de menu"""
        menubar = self.menuBar()

        # Menu Fichier
        file_menu = menubar.addMenu("Fichier")

        # Ouvrir une base de données
        open_action = QAction("Ouvrir une base de données...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_database)
        file_menu.addAction(open_action)

        # Nouvelle base de données
        new_action = QAction("Nouvelle base de données...", self)
        new_action.setShortcut("Ctrl+N")
        new_action.triggered.connect(self.new_database)
        file_menu.addAction(new_action)

        file_menu.addSeparator()

        # Quitter
        quit_action = QAction("Quitter", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

    def open_database(self):
        """Ouvre une base de données existante"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Ouvrir une base de données",
            str(Path.home()),
            "Base de données SQLite (*.db);;Tous les fichiers (*)"
        )

        if file_path:
            self.current_db_path = Path(file_path)
            self.setWindowTitle(f"Filament Colors Viewer - {self.current_db_path.name}")
            self.load_data()
            self.status_bar.showMessage(f"Base de données chargée: {file_path}")

    def new_database(self):
        """Crée une nouvelle base de données"""
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Créer une nouvelle base de données",
            str(Path.home() / "filaments.db"),
            "Base de données SQLite (*.db);;Tous les fichiers (*)"
        )

        if file_path:
            # S'assurer que le fichier a l'extension .db
            if not file_path.endswith('.db'):
                file_path += '.db'

            self.current_db_path = Path(file_path)

            # Initialiser la nouvelle base
            conn = sqlite3.connect(str(self.current_db_path))
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS filaments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT UNIQUE,
                    name TEXT,
                    manufacturer TEXT,
                    color_name TEXT,
                    material_type TEXT,
                    hex_color TEXT,
                    complement_hex TEXT,
                    rgb_r INTEGER,
                    rgb_g INTEGER,
                    rgb_b INTEGER,
                    hsl_h REAL,
                    hsl_s REAL,
                    hsl_l REAL,
                    temperature_bed TEXT,
                    temperature_hotend TEXT,
                    is_transparent INTEGER DEFAULT 0,
                    is_glitter INTEGER DEFAULT 0,
                    is_glow INTEGER DEFAULT 0,
                    notes TEXT,
                    image_url TEXT,
                    date_added TEXT,
                    td_hex TEXT,
                    amazon_link TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filament_id INTEGER,
                    tag TEXT,
                    FOREIGN KEY (filament_id) REFERENCES filaments(id)
                )
            ''')
            conn.commit()
            conn.close()

            self.setWindowTitle(f"Filament Colors Viewer - {self.current_db_path.name}")
            self.load_data()
            self.status_bar.showMessage(f"Nouvelle base de données créée: {file_path}")

    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        # Barre d'outils
        toolbar = QHBoxLayout()

        self.scrape_btn = QPushButton("Lancer le Scraping")
        self.scrape_btn.clicked.connect(self.start_scraping)
        self.scrape_btn.setStyleSheet("QPushButton { padding: 8px 16px; }")
        toolbar.addWidget(self.scrape_btn)

        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self.on_pause_resume_clicked)
        self.pause_btn.setStyleSheet("QPushButton { padding: 8px 16px; }")
        toolbar.addWidget(self.pause_btn)

        self.stop_btn = QPushButton("Arrêter")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_scraping)
        self.stop_btn.setStyleSheet("QPushButton { padding: 8px 16px; }")
        toolbar.addWidget(self.stop_btn)

        self.full_update_cb = QCheckBox("Full Update")
        self.full_update_cb.setToolTip("Si coché, re-scrape toutes les entrées même si elles sont déjà complètes")
        toolbar.addWidget(self.full_update_cb)

        toolbar.addSpacing(20)

        toolbar.addWidget(QLabel("Recherche:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Nom, fabricant, couleur...")
        self.search_input.textChanged.connect(self.filter_data)
        self.search_input.setMinimumWidth(200)
        toolbar.addWidget(self.search_input)

        toolbar.addWidget(QLabel("Type:"))
        self.type_filter = QComboBox()
        self.type_filter.addItem("Tous")
        self.type_filter.setMinimumWidth(100)
        self.type_filter.currentTextChanged.connect(self.filter_data)
        toolbar.addWidget(self.type_filter)

        toolbar.addWidget(QLabel("Fabricant:"))
        self.manufacturer_filter = QComboBox()
        self.manufacturer_filter.addItem("Tous")
        self.manufacturer_filter.setMinimumWidth(150)
        self.manufacturer_filter.currentTextChanged.connect(self.filter_data)
        toolbar.addWidget(self.manufacturer_filter)

        self.refresh_btn = QPushButton("Rafraîchir")
        self.refresh_btn.clicked.connect(self.load_data)
        toolbar.addWidget(self.refresh_btn)

        toolbar.addStretch()

        layout.addLayout(toolbar)

        # Progress bar et estimation temps
        progress_layout = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setMinimumWidth(300)
        progress_layout.addWidget(self.progress_bar)

        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet("""
            QLabel {
                font-size: 12px;
                font-weight: bold;
                padding: 5px 10px;
                background-color: #f0f0f0;
                border-radius: 3px;
            }
        """)
        self.progress_label.setMinimumHeight(30)
        progress_layout.addWidget(self.progress_label, 1)
        layout.addLayout(progress_layout)

        # Splitter principal
        splitter = QSplitter(Qt.Horizontal)

        # Table des filaments
        self.table = QTableWidget()
        self.table.setColumnCount(12)
        self.table.setHorizontalHeaderLabels([
            "#", "Couleur", "Nom", "Fabricant", "Type", "HEX", "RGB",
            "TD", "Hotend", "Lit", "Propriétés", "ID"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)  # Colonne Nom
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(self.on_selection_changed)
        self.table.setColumnHidden(11, True)  # Cacher la colonne ID
        self.table.setSortingEnabled(True)  # Activer le tri par colonnes
        self.table.horizontalHeader().setSortIndicatorShown(True)

        splitter.addWidget(self.table)

        # Détails
        self.detail_widget = FilamentDetailWidget()
        self.detail_widget.setMinimumWidth(280)
        splitter.addWidget(self.detail_widget)

        splitter.setSizes([1280, 320])  # 80% / 20%
        layout.addWidget(splitter, stretch=1)  # Le splitter prend tout l'espace disponible

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

    def load_data(self):
        """Charge les données depuis la base de données"""
        try:
            conn = sqlite3.connect(str(self.current_db_path))
            cursor = conn.cursor()

            cursor.execute('''
                SELECT id, name, manufacturer, material_type, hex_color,
                       rgb_r, rgb_g, rgb_b, td_hex, temperature_hotend, temperature_bed,
                       is_transparent, is_glitter, is_glow
                FROM filaments
                ORDER BY manufacturer, name
            ''')
            rows = cursor.fetchall()

            # Remplir les filtres (garder la sélection actuelle si possible)
            current_type = self.type_filter.currentText()
            current_mfr = self.manufacturer_filter.currentText()

            cursor.execute('SELECT DISTINCT material_type FROM filaments WHERE material_type IS NOT NULL ORDER BY material_type')
            types = [r[0] for r in cursor.fetchall()]
            self.type_filter.blockSignals(True)
            self.type_filter.clear()
            self.type_filter.addItem("Tous")
            self.type_filter.addItems(types)
            if current_type in types:
                self.type_filter.setCurrentText(current_type)
            self.type_filter.blockSignals(False)

            cursor.execute('SELECT DISTINCT manufacturer FROM filaments WHERE manufacturer IS NOT NULL ORDER BY manufacturer')
            manufacturers = [r[0] for r in cursor.fetchall()]
            self.manufacturer_filter.blockSignals(True)
            self.manufacturer_filter.clear()
            self.manufacturer_filter.addItem("Tous")
            self.manufacturer_filter.addItems(manufacturers)
            if current_mfr in manufacturers:
                self.manufacturer_filter.setCurrentText(current_mfr)
            self.manufacturer_filter.blockSignals(False)

            conn.close()

            self.populate_table(rows)
            self.status_bar.showMessage(f"{len(rows)} filaments en base de données")

        except Exception as e:
            self.status_bar.showMessage(f"Erreur: {e}")

    def populate_table(self, rows):
        """Remplit la table avec les données"""
        # Désactiver le tri pendant le remplissage pour la performance
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))

        for i, row in enumerate(rows):
            (id_, name, manufacturer, material_type, hex_color,
             rgb_r, rgb_g, rgb_b, td_hex, temp_hotend, temp_bed,
             is_transparent, is_glitter, is_glow) = row

            # Index (pour tri numérique)
            index_item = QTableWidgetItem()
            index_item.setData(Qt.DisplayRole, i + 1)  # Affiche 1, 2, 3...
            self.table.setItem(i, 0, index_item)

            # Couleur - utiliser ColorTableItem pour le tri par HSL
            color_item = ColorTableItem()
            if hex_color:
                color_item.setBackground(QColor(hex_color))
                # Convertir HEX en HSL pour tri par teinte (hue)
                qcolor = QColor(hex_color)
                h, s, l, _ = qcolor.getHsl()
                # Format pour tri: hue (3 digits) + saturation (3 digits) + lightness (3 digits)
                color_item.setData(Qt.UserRole, f"{h:03d}{s:03d}{l:03d}")
            else:
                color_item.setData(Qt.UserRole, "999999999")  # Sans couleur à la fin
            self.table.setItem(i, 1, color_item)

            self.table.setItem(i, 2, QTableWidgetItem(name or ""))
            self.table.setItem(i, 3, QTableWidgetItem(manufacturer or ""))
            self.table.setItem(i, 4, QTableWidgetItem(material_type or ""))
            self.table.setItem(i, 5, QTableWidgetItem(hex_color or ""))

            # RGB
            if rgb_r is not None:
                rgb_str = f"({rgb_r}, {rgb_g}, {rgb_b})"
            else:
                rgb_str = ""
            self.table.setItem(i, 6, QTableWidgetItem(rgb_str))

            # TD
            self.table.setItem(i, 7, QTableWidgetItem(td_hex or ""))

            # Températures
            self.table.setItem(i, 8, QTableWidgetItem(temp_hotend or ""))
            self.table.setItem(i, 9, QTableWidgetItem(temp_bed or ""))

            # Propriétés (icônes/texte)
            props = []
            if is_transparent:
                props.append("T")
            if is_glitter:
                props.append("G")
            if is_glow:
                props.append("P")
            self.table.setItem(i, 10, QTableWidgetItem(" ".join(props)))

            # ID
            self.table.setItem(i, 11, QTableWidgetItem(str(id_)))

        # Réactiver le tri
        self.table.setSortingEnabled(True)
        self.filter_data()

    def filter_data(self):
        """Filtre les données selon les critères"""
        search = self.search_input.text().lower()
        type_filter = self.type_filter.currentText()
        mfr_filter = self.manufacturer_filter.currentText()

        visible_count = 0
        for row in range(self.table.rowCount()):
            show = True

            if search:
                name = (self.table.item(row, 1).text() or "").lower()
                mfr = (self.table.item(row, 2).text() or "").lower()
                hex_col = (self.table.item(row, 4).text() or "").lower()
                if search not in name and search not in mfr and search not in hex_col:
                    show = False

            if type_filter != "Tous":
                if self.table.item(row, 3).text() != type_filter:
                    show = False

            if mfr_filter != "Tous":
                if self.table.item(row, 2).text() != mfr_filter:
                    show = False

            self.table.setRowHidden(row, not show)
            if show:
                visible_count += 1

        self.status_bar.showMessage(f"{visible_count} filaments affichés sur {self.table.rowCount()}")

    def on_selection_changed(self):
        """Appelé quand la sélection change"""
        selected = self.table.selectedItems()
        if not selected:
            return

        row = selected[0].row()
        id_item = self.table.item(row, 11)  # Colonne ID
        if not id_item:
            return

        id_ = int(id_item.text())

        try:
            conn = sqlite3.connect(str(self.current_db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM filaments WHERE id = ?', (id_,))
            row_data = cursor.fetchone()
            conn.close()

            if row_data:
                self.detail_widget.display_filament(dict(row_data))

        except Exception as e:
            print(f"Erreur: {e}")

    def check_saved_progress(self):
        """Vérifie s'il y a une progression sauvegardée et met à jour le bouton"""
        self.saved_progress = SeleniumScraperThread.load_progress()
        self.update_pause_button()

    def update_pause_button(self):
        """Met à jour le texte et l'état du bouton pause/reprendre"""
        if self.saved_progress:
            remaining = len(self.saved_progress["urls"]) - self.saved_progress["index"]
            self.pause_btn.setText(f"Reprendre ({remaining})")
            self.pause_btn.setEnabled(True)
            self.pause_btn.setStyleSheet("""
                QPushButton {
                    padding: 8px 16px;
                    background-color: #4CAF50;
                    color: white;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #45a049;
                }
            """)
            timestamp = self.saved_progress.get("timestamp", "")
            self.progress_label.setText(f"Session en pause depuis {timestamp} - {remaining} restants")
        else:
            self.pause_btn.setText("Pause")
            self.pause_btn.setStyleSheet("QPushButton { padding: 8px 16px; }")
            # Le bouton pause est désactivé quand pas de scraping en cours
            if not (self.scraper_thread and self.scraper_thread.isRunning()):
                self.pause_btn.setEnabled(False)

    def on_pause_resume_clicked(self):
        """Gère le clic sur le bouton Pause/Reprendre"""
        if self.saved_progress and not (self.scraper_thread and self.scraper_thread.isRunning()):
            # Mode Reprendre
            self.start_scraping(resume_data=self.saved_progress)
        elif self.scraper_thread and self.scraper_thread.isRunning():
            # Mode Pause
            self.scraper_thread.pause()
            self.progress_label.setText("Mise en pause et sauvegarde...")

    def start_scraping(self, resume_data=None):
        """Démarre le scraping"""
        if not resume_data:
            reply = QMessageBox.question(
                self, "Lancer le scraping",
                "Le scraping va récupérer les données depuis filamentcolors.xyz.\n"
                "Cela peut prendre plusieurs heures pour ~3000 filaments.\n\n"
                "Voulez-vous continuer?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return

        # Réinitialiser l'état
        self.saved_progress = None

        self.scrape_btn.setEnabled(False)
        self.pause_btn.setText("Pause")
        self.pause_btn.setEnabled(True)
        self.pause_btn.setStyleSheet("QPushButton { padding: 8px 16px; }")
        self.stop_btn.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.scraper_thread = SeleniumScraperThread(
            resume_data=resume_data,
            full_update=self.full_update_cb.isChecked(),
            db_path=self.current_db_path
        )
        self.scraper_thread.progress.connect(self.on_scrape_progress)
        self.scraper_thread.finished_signal.connect(self.on_scrape_finished)
        self.scraper_thread.error.connect(self.on_scrape_error)
        self.scraper_thread.paused.connect(self.on_scrape_paused)
        self.scraper_thread.filament_added.connect(self.on_filament_added)
        self.scraper_thread.start()

    def stop_scraping(self):
        """Arrête le scraping sans sauvegarder"""
        if self.scraper_thread:
            reply = QMessageBox.question(
                self, "Arrêter le scraping",
                "Voulez-vous arrêter le scraping?\n\n"
                "Utilisez 'Pause' pour sauvegarder la progression et reprendre plus tard.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self.scraper_thread.stop()
                self.progress_label.setText("Arrêt en cours...")

    def on_scrape_progress(self, current, total, message):
        """Met à jour la progression"""
        if total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(current)
        self.progress_label.setText(message)

    def on_filament_added(self, data):
        """Appelé quand un filament est ajouté/mis à jour - mise à jour temps réel"""
        filament_id = data.get('id')
        if not filament_id:
            return

        # Chercher si la ligne existe déjà dans la table
        existing_row = None
        for row in range(self.table.rowCount()):
            id_item = self.table.item(row, 11)  # Colonne ID
            if id_item and int(id_item.text()) == filament_id:
                existing_row = row
                break

        if existing_row is not None:
            # Mise à jour de la ligne existante
            row = existing_row
        else:
            # Ajouter une nouvelle ligne
            row = self.table.rowCount()
            self.table.insertRow(row)

        # Remplir les données
        # Index
        index_item = QTableWidgetItem()
        index_item.setData(Qt.DisplayRole, row + 1)
        self.table.setItem(row, 0, index_item)

        # Couleur
        hex_color = data.get('hex_color')
        color_item = ColorTableItem()
        if hex_color:
            color_item.setBackground(QColor(hex_color))
            qcolor = QColor(hex_color)
            h, s, l, _ = qcolor.getHsl()
            color_item.setData(Qt.UserRole, f"{h:03d}{s:03d}{l:03d}")
        else:
            color_item.setData(Qt.UserRole, "999999999")
        self.table.setItem(row, 1, color_item)

        self.table.setItem(row, 2, QTableWidgetItem(data.get('name') or ""))
        self.table.setItem(row, 3, QTableWidgetItem(data.get('manufacturer') or ""))
        self.table.setItem(row, 4, QTableWidgetItem(data.get('material_type') or ""))
        self.table.setItem(row, 5, QTableWidgetItem(hex_color or ""))

        # RGB
        rgb_r = data.get('rgb_r')
        if rgb_r is not None:
            rgb_str = f"({rgb_r}, {data.get('rgb_g')}, {data.get('rgb_b')})"
        else:
            rgb_str = ""
        self.table.setItem(row, 6, QTableWidgetItem(rgb_str))

        # TD
        self.table.setItem(row, 7, QTableWidgetItem(data.get('td_hex') or ""))

        # Températures
        self.table.setItem(row, 8, QTableWidgetItem(data.get('temperature_hotend') or ""))
        self.table.setItem(row, 9, QTableWidgetItem(data.get('temperature_bed') or ""))

        # Propriétés
        props = []
        if data.get('is_transparent'):
            props.append("T")
        if data.get('is_glitter'):
            props.append("G")
        if data.get('is_glow'):
            props.append("P")
        self.table.setItem(row, 10, QTableWidgetItem(" ".join(props)))

        # ID
        self.table.setItem(row, 11, QTableWidgetItem(str(filament_id)))

        # Scroller vers la nouvelle ligne et la sélectionner
        self.table.scrollToItem(self.table.item(row, 2))
        self.table.selectRow(row)

        # Mettre à jour le panneau de détails avec les données scrapées
        self.detail_widget.display_filament(data)

        # Mettre à jour le compteur
        self.status_bar.showMessage(f"{self.table.rowCount()} filaments en base de données")

    def on_scrape_finished(self, count):
        """Appelé quand le scraping est terminé"""
        self.scrape_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.progress_label.setText(f"Terminé! {count} filaments traités.")
        self.saved_progress = None
        self.update_pause_button()
        self.load_data()
        QMessageBox.information(self, "Scraping terminé",
                               f"{count} filaments ont été traités.")

    def on_scrape_paused(self, current_index, total):
        """Appelé quand le scraping est mis en pause"""
        self.scrape_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_bar.setVisible(False)

        # Recharger la progression sauvegardée
        self.saved_progress = SeleniumScraperThread.load_progress()
        self.update_pause_button()

        remaining = total - current_index
        QMessageBox.information(self, "Scraping en pause",
                               f"Progression sauvegardée!\n\n"
                               f"Position: {current_index}/{total}\n"
                               f"Restant: {remaining} filaments\n\n"
                               f"Cliquez sur 'Reprendre' pour continuer.")

    def on_scrape_error(self, error):
        """Appelé en cas d'erreur"""
        self.scrape_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.progress_label.setText("Erreur")
        # Vérifier s'il y a une sauvegarde
        self.saved_progress = SeleniumScraperThread.load_progress()
        self.update_pause_button()
        QMessageBox.critical(self, "Erreur", f"Erreur lors du scraping:\n{error}")

    def closeEvent(self, event):
        """Ferme proprement l'application"""
        if self.scraper_thread and self.scraper_thread.isRunning():
            self.scraper_thread.stop()
            self.scraper_thread.wait(5000)
        event.accept()


def main():
    # Initialiser la base de données
    init_database()

    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    # Style sombre optionnel
    palette = app.palette()
    app.setPalette(palette)

    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
