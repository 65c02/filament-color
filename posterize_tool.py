#!/usr/bin/env python3
"""
Outil de posterisation d'images avec PyQt5
Permet de charger une image, réduire le nombre de couleurs,
snapper sur les couleurs de filaments et exporter le résultat.
"""

import sys
import json
import math
from pathlib import Path
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QSpinBox, QFileDialog, QGroupBox,
    QSizePolicy, QMessageBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QTabWidget, QCheckBox
)
from PyQt5.QtGui import QPixmap, QImage, QColor, QPainter
from PyQt5.QtCore import Qt, QPoint
from PIL import Image
import io

from filament_db import FilamentDB


def get_app_dir():
    """Retourne le répertoire de l'application (compatible PyInstaller)."""
    if getattr(sys, 'frozen', False):
        # Exécutable PyInstaller
        return Path(sys.executable).parent
    else:
        # Script Python normal
        return Path(__file__).parent


# Fichier de sauvegarde des sélections
SELECTION_FILE = get_app_dir() / "filament_selection.json"
# Fichier de sauvegarde du chemin de la base de données
DB_PATH_FILE = get_app_dir() / "filament_db_path.json"


class ZoomableImageLabel(QLabel):
    """QLabel avec zoom molette et pan clic gauche."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.original_pixmap = None
        self.zoom_level = 1.0
        self.min_zoom = 0.1
        self.max_zoom = 10.0
        self.offset = QPoint(0, 0)
        self.drag_start = None
        self.setMouseTracking(True)
        self.setAlignment(Qt.AlignCenter)

    def set_pixmap(self, pixmap):
        """Définit le pixmap et réinitialise le zoom."""
        self.original_pixmap = pixmap
        self.zoom_level = 1.0
        self.offset = QPoint(0, 0)
        self.fit_to_view()

    def fit_to_view(self):
        """Ajuste le zoom pour que l'image tienne dans le widget."""
        if self.original_pixmap is None or self.original_pixmap.isNull():
            return

        available_w = self.width() - 4
        available_h = self.height() - 4

        if available_w <= 0 or available_h <= 0:
            return

        scale_w = available_w / self.original_pixmap.width()
        scale_h = available_h / self.original_pixmap.height()
        self.zoom_level = min(scale_w, scale_h)
        self.offset = QPoint(0, 0)
        self.update_display()

    def update_display(self):
        """Met à jour l'affichage avec le zoom et offset actuels."""
        if self.original_pixmap is None or self.original_pixmap.isNull():
            return

        new_w = int(self.original_pixmap.width() * self.zoom_level)
        new_h = int(self.original_pixmap.height() * self.zoom_level)

        if new_w <= 0 or new_h <= 0:
            return

        scaled = self.original_pixmap.scaled(
            new_w, new_h,
            Qt.KeepAspectRatio,
            Qt.FastTransformation
        )

        result = QPixmap(self.size())
        result.fill(Qt.transparent)

        painter = QPainter(result)
        x = (self.width() - scaled.width()) // 2 + self.offset.x()
        y = (self.height() - scaled.height()) // 2 + self.offset.y()
        painter.drawPixmap(x, y, scaled)
        painter.end()

        self.setPixmap(result)

    def wheelEvent(self, event):
        """Zoom avec la molette."""
        if self.original_pixmap is None:
            return

        delta = event.angleDelta().y()
        if delta > 0:
            factor = 1.2
        else:
            factor = 1 / 1.2

        new_zoom = self.zoom_level * factor
        new_zoom = max(self.min_zoom, min(self.max_zoom, new_zoom))

        if new_zoom != self.zoom_level:
            mouse_pos = event.pos()
            center = QPoint(self.width() // 2, self.height() // 2)
            rel_pos = mouse_pos - center - self.offset
            scale_change = new_zoom / self.zoom_level
            new_rel_pos = rel_pos * scale_change
            self.offset = self.offset - (new_rel_pos - rel_pos)

            self.zoom_level = new_zoom
            self.update_display()

    def mousePressEvent(self, event):
        """Début du drag."""
        if event.button() == Qt.LeftButton:
            self.drag_start = event.pos()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        """Déplacement pendant le drag."""
        if self.drag_start is not None:
            delta = event.pos() - self.drag_start
            self.offset += delta
            self.drag_start = event.pos()
            self.update_display()

    def mouseReleaseEvent(self, event):
        """Fin du drag."""
        if event.button() == Qt.LeftButton:
            self.drag_start = None
            self.setCursor(Qt.ArrowCursor)

    def mouseDoubleClickEvent(self, event):
        """Double-clic pour réinitialiser la vue."""
        if event.button() == Qt.LeftButton:
            self.fit_to_view()

    def resizeEvent(self, event):
        """Redimensionnement du widget."""
        super().resizeEvent(event)
        if self.original_pixmap is not None:
            self.update_display()


class PosterizeWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.original_image = None
        self.posterized_image = None
        self.filament_db = None
        self.palette_mapping = []
        self.filament_selection = {}  # {filament_id: bool}
        self.filament_checkboxes = {}  # {filament_id: QCheckBox}

        # Charger la base de données
        if not self.load_filament_db():
            # Si pas de base, on initialise quand même l'UI
            self.filament_db = FilamentDB(auto_load=False)

        self.load_selection()
        self.init_ui()

    def load_filament_db(self) -> bool:
        """Charge la base de données des filaments."""
        # Essayer de charger le chemin sauvegardé
        saved_path = None
        if DB_PATH_FILE.exists():
            try:
                with open(DB_PATH_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    saved_path = data.get('db_path')
            except Exception:
                pass

        # Essayer le chemin sauvegardé
        if saved_path and Path(saved_path).exists():
            self.filament_db = FilamentDB(db_path=saved_path)
            if self.filament_db.db_exists:
                return True

        # Essayer le chemin par défaut
        self.filament_db = FilamentDB()
        if self.filament_db.db_exists:
            return True

        return False

    def choose_database(self):
        """Ouvre un dialogue pour choisir la base de données."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Choisir la base de données des filaments",
            "",
            "Base de données SQLite (*.db);;Tous les fichiers (*)"
        )

        if file_path:
            if self.filament_db.set_db_path(file_path):
                # Sauvegarder le chemin
                try:
                    with open(DB_PATH_FILE, 'w', encoding='utf-8') as f:
                        json.dump({'db_path': file_path}, f)
                except Exception:
                    pass

                # Recharger les sélections et la table
                self.filament_selection = {}
                self.load_selection()
                self.populate_filaments_table()
                self.update_filament_count_label()
                self.update_db_info_label()

                QMessageBox.information(
                    self, "Succès",
                    f"Base de données chargée:\n{file_path}\n\n"
                    f"{self.filament_db.count()} filaments trouvés."
                )
            else:
                QMessageBox.critical(
                    self, "Erreur",
                    f"Impossible de charger la base de données:\n{file_path}"
                )

    def load_selection(self):
        """Charge les sélections depuis le fichier JSON."""
        if SELECTION_FILE.exists():
            try:
                with open(SELECTION_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.filament_selection = {int(k): v for k, v in data.items()}
            except Exception:
                self.filament_selection = {}

        # Initialiser les filaments non présents à True (sélectionné par défaut)
        if self.filament_db and self.filament_db.filaments:
            for filament in self.filament_db.filaments:
                fid = filament.get('id')
                if fid not in self.filament_selection:
                    self.filament_selection[fid] = True

    def save_selection(self):
        """Sauvegarde les sélections dans le fichier JSON."""
        try:
            with open(SELECTION_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.filament_selection, f, indent=2)
        except Exception as e:
            print(f"Erreur sauvegarde sélection: {e}")

    def on_filament_checkbox_changed(self, filament_id, state):
        """Callback quand un checkbox de filament change."""
        self.filament_selection[filament_id] = (state == Qt.Checked)
        self.save_selection()
        self.update_filament_count_label()

    def get_selected_filaments(self):
        """Retourne la liste des filaments sélectionnés."""
        if not self.filament_db or not self.filament_db.filaments:
            return []
        return [
            f for f in self.filament_db.filaments
            if self.filament_selection.get(f.get('id'), True)
        ]

    def find_closest_color_in_selection(self, r, g, b):
        """Trouve le filament le plus proche parmi les sélectionnés."""
        selected = self.get_selected_filaments()
        if not selected:
            return None

        closest = None
        min_distance = float('inf')

        for filament in selected:
            fr = filament.get('rgb_r')
            fg = filament.get('rgb_g')
            fb = filament.get('rgb_b')

            if fr is None or fg is None or fb is None:
                continue

            distance = math.sqrt((r - fr) ** 2 + (g - fg) ** 2 + (b - fb) ** 2)

            if distance < min_distance:
                min_distance = distance
                closest = filament

        return closest

    def init_ui(self):
        self.setWindowTitle("Posterize - Réduction de couleurs (Filaments)")
        self.setMinimumSize(1000, 700)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # Onglets
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # Onglet Image
        self.image_tab = QWidget()
        self.init_image_tab()
        self.tabs.addTab(self.image_tab, "Image")

        # Onglet Filaments
        self.filaments_tab = QWidget()
        self.init_filaments_tab()
        self.tabs.addTab(self.filaments_tab, "Filaments")

    def init_image_tab(self):
        """Initialise l'onglet Image."""
        layout = QVBoxLayout(self.image_tab)

        # Contrôles
        controls_group = QGroupBox("Contrôles")
        controls_layout = QHBoxLayout(controls_group)

        self.load_btn = QPushButton("Charger image")
        self.load_btn.clicked.connect(self.load_image)
        controls_layout.addWidget(self.load_btn)

        controls_layout.addWidget(QLabel("Nombre de couleurs:"))
        self.color_spinbox = QSpinBox()
        self.color_spinbox.setRange(2, 256)
        self.color_spinbox.setValue(4)
        self.color_spinbox.valueChanged.connect(self.on_color_change)
        controls_layout.addWidget(self.color_spinbox)

        self.posterize_btn = QPushButton("Posteriser")
        self.posterize_btn.clicked.connect(self.posterize_image)
        self.posterize_btn.setEnabled(False)
        controls_layout.addWidget(self.posterize_btn)

        self.export_btn = QPushButton("Exporter")
        self.export_btn.clicked.connect(self.export_image)
        self.export_btn.setEnabled(False)
        controls_layout.addWidget(self.export_btn)

        self.filament_count_label = QLabel()
        self.update_filament_count_label()
        controls_layout.addWidget(self.filament_count_label)

        controls_layout.addStretch()
        layout.addWidget(controls_group)

        # Zone d'affichage des images
        images_layout = QHBoxLayout()

        # Image originale
        original_group = QGroupBox("Image originale")
        original_layout = QVBoxLayout(original_group)
        self.original_label = ZoomableImageLabel()
        self.original_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        original_layout.addWidget(self.original_label)
        self.original_info = QLabel("")
        original_layout.addWidget(self.original_info)
        images_layout.addWidget(original_group)

        # Image posterisée
        posterized_group = QGroupBox("Image posterisée (couleurs filaments)")
        posterized_layout = QVBoxLayout(posterized_group)
        self.posterized_label = ZoomableImageLabel()
        self.posterized_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        posterized_layout.addWidget(self.posterized_label)
        self.posterized_info = QLabel("")
        posterized_layout.addWidget(self.posterized_info)
        images_layout.addWidget(posterized_group)

        layout.addLayout(images_layout)

        # Table des correspondances couleurs/filaments
        mapping_group = QGroupBox("Correspondances Palette → Filaments")
        mapping_layout = QVBoxLayout(mapping_group)

        self.mapping_table = QTableWidget()
        self.mapping_table.setColumnCount(6)
        self.mapping_table.setHorizontalHeaderLabels([
            "Index", "Couleur originale", "Couleur filament", "Nom filament", "Fabricant", "Type"
        ])
        header = self.mapping_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.Fixed)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.mapping_table.setColumnWidth(1, 115)
        self.mapping_table.setColumnWidth(2, 115)
        self.mapping_table.setColumnWidth(4, 92)
        self.mapping_table.setMaximumHeight(200)
        mapping_layout.addWidget(self.mapping_table)

        layout.addWidget(mapping_group)

    def init_filaments_tab(self):
        """Initialise l'onglet Filaments."""
        layout = QVBoxLayout(self.filaments_tab)

        # Boutons de sélection
        btn_layout = QHBoxLayout()

        choose_db_btn = QPushButton("Choisir base de données...")
        choose_db_btn.clicked.connect(self.choose_database)
        btn_layout.addWidget(choose_db_btn)

        btn_layout.addWidget(QLabel("  |  "))

        select_all_btn = QPushButton("Tout sélectionner")
        select_all_btn.clicked.connect(self.select_all_filaments)
        btn_layout.addWidget(select_all_btn)

        deselect_all_btn = QPushButton("Tout désélectionner")
        deselect_all_btn.clicked.connect(self.deselect_all_filaments)
        btn_layout.addWidget(deselect_all_btn)

        btn_layout.addStretch()

        # Info base de données
        self.db_info_label = QLabel()
        self.update_db_info_label()
        btn_layout.addWidget(self.db_info_label)

        layout.addLayout(btn_layout)

        # Message si pas de base de données
        self.no_db_label = QLabel(
            "Aucune base de données chargée.\n"
            "Cliquez sur 'Choisir base de données...' pour sélectionner un fichier."
        )
        self.no_db_label.setAlignment(Qt.AlignCenter)
        self.no_db_label.setStyleSheet("color: #888; font-size: 14px; padding: 50px;")
        layout.addWidget(self.no_db_label)

        # Table des filaments
        self.filaments_table = QTableWidget()
        self.filaments_table.setColumnCount(6)
        self.filaments_table.setHorizontalHeaderLabels([
            "", "ID", "Couleur", "Nom", "Fabricant", "Type"
        ])

        header = self.filaments_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # Checkbox
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # ID
        header.setSectionResizeMode(2, QHeaderView.Fixed)  # Couleur
        header.setSectionResizeMode(3, QHeaderView.Stretch)  # Nom
        header.setSectionResizeMode(4, QHeaderView.Fixed)  # Fabricant
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)  # Type
        self.filaments_table.setColumnWidth(2, 100)
        self.filaments_table.setColumnWidth(4, 100)

        layout.addWidget(self.filaments_table)

        # Remplir la table
        self.populate_filaments_table()

    def update_db_info_label(self):
        """Met à jour le label d'info de la base de données."""
        if self.filament_db and self.filament_db.db_exists:
            db_name = Path(self.filament_db.db_path).name
            self.db_info_label.setText(f"Base: {db_name}")
            self.db_info_label.setStyleSheet("color: green;")
        else:
            self.db_info_label.setText("Aucune base")
            self.db_info_label.setStyleSheet("color: red;")

    def populate_filaments_table(self):
        """Remplit la table des filaments."""
        # Vider les checkboxes existantes
        self.filament_checkboxes.clear()

        filaments = self.filament_db.filaments if self.filament_db else []

        # Afficher/masquer le message "pas de base"
        if hasattr(self, 'no_db_label'):
            self.no_db_label.setVisible(len(filaments) == 0)
        if hasattr(self, 'filaments_table'):
            self.filaments_table.setVisible(len(filaments) > 0)

        self.filaments_table.setRowCount(len(filaments))

        for row, filament in enumerate(filaments):
            fid = filament.get('id')

            # Checkbox
            checkbox = QCheckBox()
            checkbox.setChecked(self.filament_selection.get(fid, True))
            checkbox.stateChanged.connect(
                lambda state, fid=fid: self.on_filament_checkbox_changed(fid, state)
            )
            self.filament_checkboxes[fid] = checkbox

            checkbox_widget = QWidget()
            checkbox_layout = QHBoxLayout(checkbox_widget)
            checkbox_layout.addWidget(checkbox)
            checkbox_layout.setAlignment(Qt.AlignCenter)
            checkbox_layout.setContentsMargins(0, 0, 0, 0)
            self.filaments_table.setCellWidget(row, 0, checkbox_widget)

            # ID
            self.filaments_table.setItem(row, 1, QTableWidgetItem(str(fid)))

            # Couleur
            r = filament.get('rgb_r', 128)
            g = filament.get('rgb_g', 128)
            b = filament.get('rgb_b', 128)
            hex_color = filament.get('hex_color', '')
            color_item = QTableWidgetItem(hex_color)
            color_item.setBackground(QColor(r, g, b))
            if (r * 0.299 + g * 0.587 + b * 0.114) > 150:
                color_item.setForeground(QColor(0, 0, 0))
            else:
                color_item.setForeground(QColor(255, 255, 255))
            self.filaments_table.setItem(row, 2, color_item)

            # Nom
            self.filaments_table.setItem(row, 3, QTableWidgetItem(filament.get('name', '')))

            # Fabricant
            self.filaments_table.setItem(row, 4, QTableWidgetItem(filament.get('manufacturer', '')))

            # Type
            self.filaments_table.setItem(row, 5, QTableWidgetItem(filament.get('material_type', '')))

    def select_all_filaments(self):
        """Sélectionne tous les filaments."""
        for fid, checkbox in self.filament_checkboxes.items():
            checkbox.setChecked(True)

    def deselect_all_filaments(self):
        """Désélectionne tous les filaments."""
        for fid, checkbox in self.filament_checkboxes.items():
            checkbox.setChecked(False)

    def update_filament_count_label(self):
        """Met à jour le label du nombre de filaments sélectionnés."""
        if self.filament_db and self.filament_db.db_exists:
            selected = len(self.get_selected_filaments())
            total = self.filament_db.count()
            self.filament_count_label.setText(f"| Filaments: {selected}/{total}")
        else:
            self.filament_count_label.setText("| Aucune base de données")

    def load_image(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Charger une image",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.tiff);;Tous les fichiers (*)"
        )
        if file_path:
            try:
                self.original_image = Image.open(file_path)
                if self.original_image.mode in ('RGBA', 'LA'):
                    background = Image.new('RGB', self.original_image.size, (255, 255, 255))
                    if self.original_image.mode == 'RGBA':
                        background.paste(self.original_image, mask=self.original_image.split()[3])
                    else:
                        background.paste(self.original_image, mask=self.original_image.split()[1])
                    self.original_image = background
                elif self.original_image.mode != 'RGB':
                    self.original_image = self.original_image.convert('RGB')

                self.display_image(self.original_image, self.original_label)
                self.original_info.setText(
                    f"Taille: {self.original_image.width}x{self.original_image.height} | "
                    f"Mode: {self.original_image.mode}"
                )
                self.posterize_btn.setEnabled(True)
                self.posterized_label.setText("Cliquez sur 'Posteriser'")
                self.posterized_info.setText("")
                self.posterized_image = None
                self.export_btn.setEnabled(False)
                self.palette_mapping = []
                self.mapping_table.setRowCount(0)
            except Exception as e:
                QMessageBox.critical(self, "Erreur", f"Impossible de charger l'image:\n{e}")

    def posterize_image(self):
        if self.original_image is None:
            return

        # Vérifier qu'une base de données est chargée
        if not self.filament_db or not self.filament_db.db_exists:
            QMessageBox.warning(
                self, "Attention",
                "Aucune base de données chargée.\n"
                "Veuillez choisir une base de données dans l'onglet 'Filaments'."
            )
            self.tabs.setCurrentWidget(self.filaments_tab)
            return

        # Vérifier qu'il y a des filaments sélectionnés
        if not self.get_selected_filaments():
            QMessageBox.warning(
                self, "Attention",
                "Aucun filament sélectionné.\nVeuillez sélectionner des filaments dans l'onglet 'Filaments'."
            )
            return

        num_colors = self.color_spinbox.value()

        try:
            quantized = self.original_image.quantize(
                colors=num_colors,
                method=Image.Quantize.MEDIANCUT,
                dither=Image.Dither.NONE
            )

            palette = quantized.getpalette()
            used_indices = set(quantized.getdata())

            self.palette_mapping = []
            new_palette = list(palette)

            for idx in sorted(used_indices):
                if idx < len(palette) // 3:
                    orig_r = palette[idx * 3]
                    orig_g = palette[idx * 3 + 1]
                    orig_b = palette[idx * 3 + 2]

                    # Trouver le filament le plus proche parmi les sélectionnés
                    filament = self.find_closest_color_in_selection(orig_r, orig_g, orig_b)

                    if filament:
                        snap_r = filament.get('rgb_r', orig_r)
                        snap_g = filament.get('rgb_g', orig_g)
                        snap_b = filament.get('rgb_b', orig_b)

                        new_palette[idx * 3] = snap_r
                        new_palette[idx * 3 + 1] = snap_g
                        new_palette[idx * 3 + 2] = snap_b

                        self.palette_mapping.append({
                            'index': idx,
                            'original_color': {'r': orig_r, 'g': orig_g, 'b': orig_b},
                            'snapped_color': {'r': snap_r, 'g': snap_g, 'b': snap_b},
                            'filament': {
                                'id': filament.get('id'),
                                'name': filament.get('name'),
                                'manufacturer': filament.get('manufacturer'),
                                'material_type': filament.get('material_type'),
                                'hex_color': filament.get('hex_color')
                            }
                        })
                    else:
                        self.palette_mapping.append({
                            'index': idx,
                            'original_color': {'r': orig_r, 'g': orig_g, 'b': orig_b},
                            'snapped_color': {'r': orig_r, 'g': orig_g, 'b': orig_b},
                            'filament': None
                        })

            quantized.putpalette(new_palette)
            self.posterized_image = quantized

            display_img = self.posterized_image.convert('RGB')
            self.display_image(display_img, self.posterized_label)

            self.posterized_info.setText(
                f"Taille: {self.posterized_image.width}x{self.posterized_image.height} | "
                f"Mode: P (palette) | Couleurs: {len(self.palette_mapping)}"
            )

            self.export_btn.setEnabled(True)
            self.update_mapping_table()

        except Exception as e:
            QMessageBox.critical(self, "Erreur", f"Erreur lors de la posterisation:\n{e}")

    def update_mapping_table(self):
        """Met à jour la table des correspondances."""
        self.mapping_table.setRowCount(len(self.palette_mapping))

        for row, mapping in enumerate(self.palette_mapping):
            self.mapping_table.setItem(row, 0, QTableWidgetItem(str(mapping['index'])))

            orig = mapping['original_color']
            orig_item = QTableWidgetItem(f"RGB({orig['r']}, {orig['g']}, {orig['b']})")
            orig_item.setBackground(QColor(orig['r'], orig['g'], orig['b']))
            if (orig['r'] * 0.299 + orig['g'] * 0.587 + orig['b'] * 0.114) > 150:
                orig_item.setForeground(QColor(0, 0, 0))
            else:
                orig_item.setForeground(QColor(255, 255, 255))
            self.mapping_table.setItem(row, 1, orig_item)

            snap = mapping['snapped_color']
            snap_item = QTableWidgetItem(f"RGB({snap['r']}, {snap['g']}, {snap['b']})")
            snap_item.setBackground(QColor(snap['r'], snap['g'], snap['b']))
            if (snap['r'] * 0.299 + snap['g'] * 0.587 + snap['b'] * 0.114) > 150:
                snap_item.setForeground(QColor(0, 0, 0))
            else:
                snap_item.setForeground(QColor(255, 255, 255))
            self.mapping_table.setItem(row, 2, snap_item)

            filament = mapping['filament']
            if filament:
                self.mapping_table.setItem(row, 3, QTableWidgetItem(filament.get('name', '')))
                self.mapping_table.setItem(row, 4, QTableWidgetItem(filament.get('manufacturer', '')))
                self.mapping_table.setItem(row, 5, QTableWidgetItem(filament.get('material_type', '')))
            else:
                self.mapping_table.setItem(row, 3, QTableWidgetItem("N/A"))
                self.mapping_table.setItem(row, 4, QTableWidgetItem(""))
                self.mapping_table.setItem(row, 5, QTableWidgetItem(""))

    def display_image(self, pil_image, label):
        """Convertit une image PIL en QPixmap et l'affiche dans un ZoomableImageLabel."""
        buffer = io.BytesIO()
        pil_image.save(buffer, format='PNG')
        buffer.seek(0)

        qimage = QImage()
        qimage.loadFromData(buffer.getvalue())
        pixmap = QPixmap.fromImage(qimage)

        label.set_pixmap(pixmap)

    def export_image(self):
        if self.posterized_image is None:
            return

        file_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Exporter l'image",
            "",
            "PNG (*.png);;GIF (*.gif);;BMP (*.bmp);;TIFF (*.tiff)"
        )

        if file_path:
            try:
                path = Path(file_path)
                if not path.suffix:
                    if "PNG" in selected_filter:
                        file_path += ".png"
                    elif "GIF" in selected_filter:
                        file_path += ".gif"
                    elif "BMP" in selected_filter:
                        file_path += ".bmp"
                    elif "TIFF" in selected_filter:
                        file_path += ".tiff"

                self.posterized_image.save(file_path)

                json_path = Path(file_path).with_suffix('.json')

                export_data = {
                    'image_file': Path(file_path).name,
                    'num_colors': len(self.palette_mapping),
                    'palette_mapping': []
                }

                for mapping in self.palette_mapping:
                    entry = {
                        'palette_index': mapping['index'],
                        'original_rgb': [
                            mapping['original_color']['r'],
                            mapping['original_color']['g'],
                            mapping['original_color']['b']
                        ],
                        'filament_rgb': [
                            mapping['snapped_color']['r'],
                            mapping['snapped_color']['g'],
                            mapping['snapped_color']['b']
                        ]
                    }

                    if mapping['filament']:
                        entry['filament'] = {
                            'id': mapping['filament']['id'],
                            'name': mapping['filament']['name'],
                            'manufacturer': mapping['filament']['manufacturer'],
                            'material_type': mapping['filament']['material_type'],
                            'hex_color': mapping['filament']['hex_color']
                        }
                    else:
                        entry['filament'] = None

                    export_data['palette_mapping'].append(entry)

                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(export_data, f, indent=2, ensure_ascii=False)

                QMessageBox.information(
                    self, "Succès",
                    f"Image exportée:\n{file_path}\n\n"
                    f"Correspondances filaments:\n{json_path}"
                )
            except Exception as e:
                QMessageBox.critical(self, "Erreur", f"Erreur lors de l'export:\n{e}")

    def on_color_change(self):
        """Callback quand le nombre de couleurs change."""
        if self.posterized_image is not None:
            self.posterize_image()


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    window = PosterizeWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
