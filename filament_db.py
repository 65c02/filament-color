#!/usr/bin/env python3
"""
Classe pour accéder à la base de données des filaments
"""

import sqlite3
import math
from pathlib import Path
from typing import Optional, Dict, List, Tuple


class FilamentDB:
    """Classe pour accéder à la base de données des filaments"""

    def __init__(self, db_path: str = None):
        """
        Charge la base de données des filaments.

        Args:
            db_path: Chemin vers la base de données. Si None, utilise le chemin par défaut.
        """
        if db_path is None:
            db_path = Path(__file__).parent / "filaments.db"

        self.db_path = str(db_path)
        self.filaments: List[Dict] = []
        self._load_database()

    def _load_database(self):
        """Charge tous les filaments depuis la base de données"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM filaments ORDER BY id
        ''')

        self.filaments = [dict(row) for row in cursor.fetchall()]
        conn.close()

    def reload(self):
        """Recharge la base de données"""
        self._load_database()

    def count(self) -> int:
        """Retourne le nombre total de filaments"""
        return len(self.filaments)

    def get(self, n: int) -> Optional[Dict]:
        """
        Retourne la n-ième couleur (0-indexed).

        Args:
            n: Index de la couleur (0 à count()-1)

        Returns:
            Dictionnaire avec les données du filament, ou None si index invalide
        """
        if 0 <= n < len(self.filaments):
            return self.filaments[n]
        return None

    def get_by_id(self, filament_id: int) -> Optional[Dict]:
        """
        Retourne un filament par son ID.

        Args:
            filament_id: ID du filament dans la base

        Returns:
            Dictionnaire avec les données du filament, ou None si non trouvé
        """
        for filament in self.filaments:
            if filament['id'] == filament_id:
                return filament
        return None

    def find_closest_color(self, r: int, g: int, b: int) -> Optional[Dict]:
        """
        Trouve la couleur la plus proche d'une couleur RGB donnée.

        Utilise la distance euclidienne dans l'espace RGB.

        Args:
            r: Composante rouge (0-255)
            g: Composante verte (0-255)
            b: Composante bleue (0-255)

        Returns:
            Dictionnaire avec les données du filament le plus proche, ou None si base vide
        """
        if not self.filaments:
            return None

        closest = None
        min_distance = float('inf')

        for filament in self.filaments:
            fr = filament.get('rgb_r')
            fg = filament.get('rgb_g')
            fb = filament.get('rgb_b')

            # Ignorer les filaments sans couleur RGB
            if fr is None or fg is None or fb is None:
                continue

            # Distance euclidienne
            distance = math.sqrt(
                (r - fr) ** 2 +
                (g - fg) ** 2 +
                (b - fb) ** 2
            )

            if distance < min_distance:
                min_distance = distance
                closest = filament

        return closest

    def find_closest_color_hex(self, hex_color: str) -> Optional[Dict]:
        """
        Trouve la couleur la plus proche d'une couleur HEX donnée.

        Args:
            hex_color: Couleur au format "#RRGGBB" ou "RRGGBB"

        Returns:
            Dictionnaire avec les données du filament le plus proche
        """
        hex_color = hex_color.lstrip('#')
        if len(hex_color) != 6:
            raise ValueError("Format HEX invalide. Utilisez '#RRGGBB' ou 'RRGGBB'")

        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)

        return self.find_closest_color(r, g, b)

    def find_n_closest_colors(self, r: int, g: int, b: int, n: int = 5) -> List[Tuple[Dict, float]]:
        """
        Trouve les n couleurs les plus proches d'une couleur RGB donnée.

        Args:
            r: Composante rouge (0-255)
            g: Composante verte (0-255)
            b: Composante bleue (0-255)
            n: Nombre de résultats à retourner

        Returns:
            Liste de tuples (filament, distance) triés par distance croissante
        """
        results = []

        for filament in self.filaments:
            fr = filament.get('rgb_r')
            fg = filament.get('rgb_g')
            fb = filament.get('rgb_b')

            if fr is None or fg is None or fb is None:
                continue

            distance = math.sqrt(
                (r - fr) ** 2 +
                (g - fg) ** 2 +
                (b - fb) ** 2
            )
            results.append((filament, distance))

        # Trier par distance et retourner les n premiers
        results.sort(key=lambda x: x[1])
        return results[:n]

    def search(self, query: str) -> List[Dict]:
        """
        Recherche des filaments par nom, fabricant ou couleur.

        Args:
            query: Texte à rechercher

        Returns:
            Liste des filaments correspondants
        """
        query = query.lower()
        results = []

        for filament in self.filaments:
            name = (filament.get('name') or '').lower()
            manufacturer = (filament.get('manufacturer') or '').lower()
            hex_color = (filament.get('hex_color') or '').lower()

            if query in name or query in manufacturer or query in hex_color:
                results.append(filament)

        return results

    def filter_by_type(self, material_type: str) -> List[Dict]:
        """
        Filtre les filaments par type de matériau.

        Args:
            material_type: Type de matériau (ex: "PLA", "PETG", "ABS")

        Returns:
            Liste des filaments de ce type
        """
        material_type = material_type.lower()
        return [
            f for f in self.filaments
            if f.get('material_type') and material_type in f['material_type'].lower()
        ]

    def filter_by_manufacturer(self, manufacturer: str) -> List[Dict]:
        """
        Filtre les filaments par fabricant.

        Args:
            manufacturer: Nom du fabricant

        Returns:
            Liste des filaments de ce fabricant
        """
        manufacturer = manufacturer.lower()
        return [
            f for f in self.filaments
            if f.get('manufacturer') and manufacturer in f['manufacturer'].lower()
        ]

    def get_all_manufacturers(self) -> List[str]:
        """Retourne la liste de tous les fabricants"""
        manufacturers = set()
        for f in self.filaments:
            if f.get('manufacturer'):
                manufacturers.add(f['manufacturer'])
        return sorted(manufacturers)

    def get_all_material_types(self) -> List[str]:
        """Retourne la liste de tous les types de matériaux"""
        types = set()
        for f in self.filaments:
            if f.get('material_type'):
                types.add(f['material_type'])
        return sorted(types)

    def filter_by_td(self, has_td: bool = True) -> List[Dict]:
        """
        Filtre les filaments selon la présence d'une valeur TD.

        Args:
            has_td: Si True, retourne les filaments avec TD. Si False, ceux sans TD.

        Returns:
            Liste des filaments filtrés
        """
        if has_td:
            return [f for f in self.filaments if f.get('td_hex')]
        else:
            return [f for f in self.filaments if not f.get('td_hex')]

    def find_closest_td(self, r: int, g: int, b: int) -> Optional[Dict]:
        """
        Trouve le filament dont le TD est le plus proche d'une couleur RGB donnée.

        Args:
            r: Composante rouge (0-255)
            g: Composante verte (0-255)
            b: Composante bleue (0-255)

        Returns:
            Dictionnaire avec les données du filament, ou None si aucun TD trouvé
        """
        closest = None
        min_distance = float('inf')

        for filament in self.filaments:
            td_hex = filament.get('td_hex')
            if not td_hex or not td_hex.startswith('#') or len(td_hex) != 7:
                continue

            # Convertir TD hex en RGB
            try:
                td_r = int(td_hex[1:3], 16)
                td_g = int(td_hex[3:5], 16)
                td_b = int(td_hex[5:7], 16)
            except ValueError:
                continue

            distance = math.sqrt(
                (r - td_r) ** 2 +
                (g - td_g) ** 2 +
                (b - td_b) ** 2
            )

            if distance < min_distance:
                min_distance = distance
                closest = filament

        return closest

    def find_closest_td_hex(self, hex_color: str) -> Optional[Dict]:
        """
        Trouve le filament dont le TD est le plus proche d'une couleur HEX donnée.

        Args:
            hex_color: Couleur au format "#RRGGBB" ou "RRGGBB"

        Returns:
            Dictionnaire avec les données du filament le plus proche
        """
        hex_color = hex_color.lstrip('#')
        if len(hex_color) != 6:
            raise ValueError("Format HEX invalide. Utilisez '#RRGGBB' ou 'RRGGBB'")

        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)

        return self.find_closest_td(r, g, b)

    def find_n_closest_td(self, r: int, g: int, b: int, n: int = 5) -> List[Tuple[Dict, float]]:
        """
        Trouve les n filaments dont le TD est le plus proche d'une couleur RGB.

        Args:
            r: Composante rouge (0-255)
            g: Composante verte (0-255)
            b: Composante bleue (0-255)
            n: Nombre de résultats à retourner

        Returns:
            Liste de tuples (filament, distance) triés par distance croissante
        """
        results = []

        for filament in self.filaments:
            td_hex = filament.get('td_hex')
            if not td_hex or not td_hex.startswith('#') or len(td_hex) != 7:
                continue

            try:
                td_r = int(td_hex[1:3], 16)
                td_g = int(td_hex[3:5], 16)
                td_b = int(td_hex[5:7], 16)
            except ValueError:
                continue

            distance = math.sqrt(
                (r - td_r) ** 2 +
                (g - td_g) ** 2 +
                (b - td_b) ** 2
            )
            results.append((filament, distance))

        results.sort(key=lambda x: x[1])
        return results[:n]


# Exemple d'utilisation
if __name__ == "__main__":
    db = FilamentDB()

    print(f"Nombre de filaments: {db.count()}")

    # Récupérer le premier filament
    first = db.get(0)
    if first:
        print(f"\nPremier filament:")
        print(f"  Nom: {first.get('name')}")
        print(f"  Fabricant: {first.get('manufacturer')}")
        print(f"  Couleur: {first.get('hex_color')}")

    # Trouver la couleur la plus proche de rouge pur
    print(f"\nCouleur la plus proche de rouge (255, 0, 0):")
    closest = db.find_closest_color(255, 0, 0)
    if closest:
        print(f"  Nom: {closest.get('name')}")
        print(f"  Fabricant: {closest.get('manufacturer')}")
        print(f"  Couleur: {closest.get('hex_color')}")
        print(f"  RGB: ({closest.get('rgb_r')}, {closest.get('rgb_g')}, {closest.get('rgb_b')})")

    # Trouver les 5 couleurs les plus proches
    print(f"\n5 couleurs les plus proches de bleu (0, 0, 255):")
    closest_5 = db.find_n_closest_colors(0, 0, 255, 5)
    for filament, distance in closest_5:
        print(f"  {filament.get('name')} - {filament.get('hex_color')} (distance: {distance:.1f})")

    # Filtrer par TD
    with_td = db.filter_by_td(has_td=True)
    print(f"\nFilaments avec TD: {len(with_td)}")

    # Trouver le TD le plus proche d'une couleur
    print(f"\nTD le plus proche de blanc (255, 255, 255):")
    closest_td = db.find_closest_td(255, 255, 255)
    if closest_td:
        print(f"  Nom: {closest_td.get('name')}")
        print(f"  TD: {closest_td.get('td_hex')}")
