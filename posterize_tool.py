#!/usr/bin/env python3
"""
Outil de posterisation d'images avec PyQt5
Permet de charger une image, réduire le nombre de couleurs,
snapper sur les couleurs de filaments et exporter le résultat.
"""

import sys
import json
from pathlib import Path
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QSpinBox, QFileDialog, QScrollArea, QGroupBox,
    QSizePolicy, QMessageBox, QTableWidget, QTableWidgetItem, QHeaderView
)
from PyQt5.QtGui import QPixmap, QImage, QColor, QPainter
from PyQt5.QtCore import Qt, QPoint
from PIL import Image
import io

from filament_db import FilamentDB


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

        # Calculer le zoom pour tenir dans l'espace disponible
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

        # Créer un pixmap avec l'offset
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

        # Facteur de zoom
        delta = event.angleDelta().y()
        if delta > 0:
            factor = 1.2
        else:
            factor = 1 / 1.2

        new_zoom = self.zoom_level * factor
        new_zoom = max(self.min_zoom, min(self.max_zoom, new_zoom))

        # Ajuster l'offset pour zoomer vers le curseur
        if new_zoom != self.zoom_level:
            mouse_pos = event.pos()
            center = QPoint(self.width() // 2, self.height() // 2)

            # Position relative au centre de l'image
            rel_pos = mouse_pos - center - self.offset

            # Ajuster l'offset
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
        self.filament_db = FilamentDB()
        self.palette_mapping = []  # Liste de {index, filament, original_color, snapped_color}
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("Posterize - Réduction de couleurs (Filaments)")
        self.setMinimumSize(1000, 700)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # Contrôles
        controls_group = QGroupBox("Contrôles")
        controls_layout = QHBoxLayout(controls_group)

        # Bouton charger
        self.load_btn = QPushButton("Charger image")
        self.load_btn.clicked.connect(self.load_image)
        controls_layout.addWidget(self.load_btn)

        # Sélecteur nombre de couleurs
        controls_layout.addWidget(QLabel("Nombre de couleurs:"))
        self.color_spinbox = QSpinBox()
        self.color_spinbox.setRange(2, 256)
        self.color_spinbox.setValue(4)
        self.color_spinbox.valueChanged.connect(self.on_color_change)
        controls_layout.addWidget(self.color_spinbox)

        # Bouton posteriser
        self.posterize_btn = QPushButton("Posteriser")
        self.posterize_btn.clicked.connect(self.posterize_image)
        self.posterize_btn.setEnabled(False)
        controls_layout.addWidget(self.posterize_btn)

        # Bouton exporter
        self.export_btn = QPushButton("Exporter")
        self.export_btn.clicked.connect(self.export_image)
        self.export_btn.setEnabled(False)
        controls_layout.addWidget(self.export_btn)

        # Info filaments
        filament_count = self.filament_db.count()
        controls_layout.addWidget(QLabel(f"| Filaments disponibles: {filament_count}"))

        controls_layout.addStretch()
        main_layout.addWidget(controls_group)

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

        main_layout.addLayout(images_layout)

        # Table des correspondances couleurs/filaments
        mapping_group = QGroupBox("Correspondances Palette → Filaments")
        mapping_layout = QVBoxLayout(mapping_group)

        self.mapping_table = QTableWidget()
        self.mapping_table.setColumnCount(6)
        self.mapping_table.setHorizontalHeaderLabels([
            "Index", "Couleur originale", "Couleur filament", "Nom filament", "Fabricant", "Type"
        ])
        header = self.mapping_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # Index
        header.setSectionResizeMode(1, QHeaderView.Fixed)  # Couleur originale
        header.setSectionResizeMode(2, QHeaderView.Fixed)  # Couleur filament
        header.setSectionResizeMode(3, QHeaderView.Stretch)  # Nom filament
        header.setSectionResizeMode(4, QHeaderView.Fixed)  # Fabricant
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)  # Type
        self.mapping_table.setColumnWidth(1, 115)  # Couleur originale
        self.mapping_table.setColumnWidth(2, 115)  # Couleur filament
        self.mapping_table.setColumnWidth(4, 92)   # Fabricant
        self.mapping_table.setMaximumHeight(200)
        mapping_layout.addWidget(self.mapping_table)

        main_layout.addWidget(mapping_group)

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
                # Convertir en RGB si nécessaire
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

        num_colors = self.color_spinbox.value()

        try:
            # Étape 1: Quantification pour réduire à N couleurs
            quantized = self.original_image.quantize(
                colors=num_colors,
                method=Image.Quantize.MEDIANCUT,
                dither=Image.Dither.NONE
            )

            # Étape 2: Extraire la palette et mapper vers les couleurs de filaments
            palette = quantized.getpalette()
            used_indices = set(quantized.getdata())

            self.palette_mapping = []
            new_palette = list(palette)  # Copie de la palette

            for idx in sorted(used_indices):
                if idx < len(palette) // 3:
                    # Couleur originale de la palette
                    orig_r = palette[idx * 3]
                    orig_g = palette[idx * 3 + 1]
                    orig_b = palette[idx * 3 + 2]

                    # Trouver le filament le plus proche
                    filament = self.filament_db.find_closest_color(orig_r, orig_g, orig_b)

                    if filament:
                        snap_r = filament.get('rgb_r', orig_r)
                        snap_g = filament.get('rgb_g', orig_g)
                        snap_b = filament.get('rgb_b', orig_b)

                        # Mettre à jour la palette avec la couleur du filament
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
                        # Pas de filament trouvé, garder la couleur originale
                        self.palette_mapping.append({
                            'index': idx,
                            'original_color': {'r': orig_r, 'g': orig_g, 'b': orig_b},
                            'snapped_color': {'r': orig_r, 'g': orig_g, 'b': orig_b},
                            'filament': None
                        })

            # Appliquer la nouvelle palette à l'image
            quantized.putpalette(new_palette)
            self.posterized_image = quantized

            # Afficher l'image posterisée
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
            # Index
            self.mapping_table.setItem(row, 0, QTableWidgetItem(str(mapping['index'])))

            # Couleur originale
            orig = mapping['original_color']
            orig_item = QTableWidgetItem(f"RGB({orig['r']}, {orig['g']}, {orig['b']})")
            orig_item.setBackground(QColor(orig['r'], orig['g'], orig['b']))
            # Texte contrasté
            if (orig['r'] * 0.299 + orig['g'] * 0.587 + orig['b'] * 0.114) > 150:
                orig_item.setForeground(QColor(0, 0, 0))
            else:
                orig_item.setForeground(QColor(255, 255, 255))
            self.mapping_table.setItem(row, 1, orig_item)

            # Couleur filament
            snap = mapping['snapped_color']
            snap_item = QTableWidgetItem(f"RGB({snap['r']}, {snap['g']}, {snap['b']})")
            snap_item.setBackground(QColor(snap['r'], snap['g'], snap['b']))
            if (snap['r'] * 0.299 + snap['g'] * 0.587 + snap['b'] * 0.114) > 150:
                snap_item.setForeground(QColor(0, 0, 0))
            else:
                snap_item.setForeground(QColor(255, 255, 255))
            self.mapping_table.setItem(row, 2, snap_item)

            # Info filament
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
                # Ajouter l'extension si manquante
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

                # Sauvegarder l'image
                self.posterized_image.save(file_path)

                # Créer le fichier JSON avec les correspondances
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
