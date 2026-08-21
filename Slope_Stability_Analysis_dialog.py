# -*- coding: utf-8 -*-
import os
from datetime import datetime
from qgis.PyQt import uic, QtWidgets
from qgis.PyQt.QtCore import Qt, QVariant, QSize, QEvent, QObject
from qgis.PyQt.QtWidgets import (
    QMessageBox, QProgressDialog, QApplication,
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton,
    QLabel, QSizePolicy, QToolBar, QAction, QFileDialog,
    QWidget, QGroupBox, QRadioButton, QComboBox, QMenu, QTabWidget,
)
from qgis.PyQt.QtGui import QColor, QIcon

from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsField,
    QgsMapLayerProxyModel,
    QgsSingleSymbolRenderer,
    QgsLineSymbol,
    QgsMarkerSymbol,
)
from qgis.gui import QgsDoubleSpinBox, QgsMapLayerComboBox

# Matplotlib Qt backend
from matplotlib.backends.backend_qt5agg import (
    FigureCanvasQTAgg as FigureCanvas,
    NavigationToolbar2QT as NavigationToolbar,
)
import matplotlib.pyplot as plt

from .processing import run_full_analysis, plot_results


# ── Muat file UI ──────────────────────────────────────────────────────────────
FORM_CLASS, _ = uic.loadUiType(
    os.path.join(os.path.dirname(__file__), 'Slope_Stability_Analysis_dialog_base.ui')
)

# (Pilihan Distribusi: Normal, Lognormal, Uniform)
DISTRIBUSI = ["Normal", "Lognormal", "Uniform"]

# Prefix objectName untuk 3 properti material di dalam tiap tab
# (dipakai _wire_distribution_std_toggle & _find_by_prefix)
MATERIAL_PROPS = ["Weight", "Cohesion", "FA"]

# =============================================================================
# PLOT DIALOG
# =============================================================================
class PlotDialog(QDialog):

    def __init__(self, fig, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Analysis Results – Bishop Monte Carlo")
        self.resize(1100, 620)
        self.setWindowFlags(
            Qt.Window |
            Qt.WindowMinimizeButtonHint |
            Qt.WindowMaximizeButtonHint |
            Qt.WindowCloseButtonHint
        )
        self._fig = fig
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self._canvas = FigureCanvas(self._fig)
        self._canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._canvas.updateGeometry()

        self._toolbar = NavigationToolbar(self._canvas, self)

        btn_save = QPushButton("  Save Image (PNG / PDF)")
        btn_save.setMinimumHeight(32)
        btn_save.clicked.connect(self._on_save)

        btn_close = QPushButton("Close")
        btn_close.setMinimumHeight(32)
        btn_close.clicked.connect(self.close)

        btn_row = QHBoxLayout()
        btn_row.addWidget(btn_save)
        btn_row.addStretch()
        btn_row.addWidget(btn_close)

        layout.addWidget(self._toolbar)
        layout.addWidget(self._canvas)
        layout.addLayout(btn_row)

    def _on_save(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Image",
            "slope_stability_results.png",
            "PNG (*.png);;PDF (*.pdf);;SVG (*.svg)"
        )
        if path:
            self._fig.savefig(path, dpi=180, bbox_inches='tight')
            QMessageBox.information(self, "Saved", f"Image saved:\n{path}")


# =============================================================================
# FOCUS TRACKER
# =============================================================================
class _RelFocusTracker(QObject):

    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog

    def eventFilter(self, obj, event):
        if event.type() == QEvent.FocusIn:
            info = self.dialog._rel_widget_map.get(obj)
            if info is not None:
                tab_widget, prefix = info
                self.dialog._last_focused_prefix[tab_widget] = prefix
        return False


# =============================================================================
# TAB BAR WHEEL SCROLLER
# =============================================================================
class _TabBarWheelScroller(QObject):

    def __init__(self, tab_widget):
        super().__init__(tab_widget)
        self.tab_widget = tab_widget

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Wheel:
            delta = event.angleDelta().y()
            if delta == 0:
                delta = event.angleDelta().x()  # dukung mouse dengan scroll horizontal

            count = self.tab_widget.count()
            if count > 1 and delta != 0:
                current = self.tab_widget.currentIndex()
                step = -1 if delta > 0 else 1
                # di tab paling awal / paling akhir, tidak muter balik.
                new_index = max(0, min(count - 1, current + step))
                self.tab_widget.setCurrentIndex(new_index)
            return True
        return False


# =============================================================================
# MULTI PLOT DIALOG  –  jendela plot, satu tab per cross section
# =============================================================================
class MultiPlotDialog(QDialog):


    def __init__(self, figures_by_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Analysis Results – Bishop Monte Carlo")
        self.resize(1150, 660)
        self.setWindowFlags(
            Qt.Window |
            Qt.WindowMinimizeButtonHint |
            Qt.WindowMaximizeButtonHint |
            Qt.WindowCloseButtonHint
        )
        self._figures_by_name = figures_by_name
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self._tabs = QTabWidget()

        for name, fig in self._figures_by_name.items():
            tab = QWidget()
            tab_layout = QVBoxLayout(tab)
            tab_layout.setContentsMargins(0, 0, 0, 0)

            canvas = FigureCanvas(fig)
            canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            canvas.updateGeometry()

            toolbar = NavigationToolbar(canvas, tab)

            tab_layout.addWidget(toolbar)
            tab_layout.addWidget(canvas)
            self._tabs.addTab(tab, name)

        layout.addWidget(self._tabs)

        btn_save = QPushButton("  Save Active Tab Image (PNG / PDF)")
        btn_save.setMinimumHeight(32)
        btn_save.clicked.connect(self._on_save_current)

        btn_close = QPushButton("Close")
        btn_close.setMinimumHeight(32)
        btn_close.clicked.connect(self.close)

        btn_row = QHBoxLayout()
        btn_row.addWidget(btn_save)
        btn_row.addStretch()
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

    def _on_save_current(self):
        current_name = self._tabs.tabText(self._tabs.currentIndex())
        fig = self._figures_by_name.get(current_name)
        if fig is None:
            return

        safe_name = current_name.replace(" ", "_")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Image",
            f"slope_stability_{safe_name}.png",
            "PNG (*.png);;PDF (*.pdf);;SVG (*.svg)"
        )
        if path:
            fig.savefig(path, dpi=180, bbox_inches='tight')
            QMessageBox.information(self, "Saved", f"Image saved:\n{path}")


# =============================================================================
# DIALOG UTAMA
# =============================================================================
class Slope_Stability_AnalysisDialog(QtWidgets.QDialog, FORM_CLASS):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)

        # ── State untuk fitur Auto Rel. Min/Rel. Max (lacak baris terfokus)
        self._rel_widget_map = {}       # {spinbox: (tab_widget, prefix)}
        self._last_focused_prefix = {}  # {tab_widget: prefix terakhir difokus}
        self._focus_tracker = _RelFocusTracker(self)

        try:
            from .ui_patch import patch_ui_critical_fixes
            patch_ui_critical_fixes(self)
        except ImportError:
            self._manual_fix_defaults()

        self._setup_layer_filters()
        self._setup_tab_widget()          # Kelola tambah/hapus/tutup tab
        self._setup_connections()

        self._plot_dlg = None
        self._last_result = None
        self._last_results = {}  # {nama_cross_section: result} — hasil multi-tab terakhir
        self._last_crs = None

    def _manual_fix_defaults(self):

        self.Interval_mQgsDoubleSpinBox.setMinimum(0.10)
        self.Interval_mQgsDoubleSpinBox.setMaximum(10.00)
        self.Interval_mQgsDoubleSpinBox.setValue(1.00)

        self.Weight_mQgsDoubleSpinBox.setMinimum(0.0)
        self.Weight_mQgsDoubleSpinBox.setMaximum(9999.9)
        self.Weight_mQgsDoubleSpinBox.setValue(0.0)

        self.Cohesion_mQgsDoubleSpinBox.setMinimum(0.0)
        self.Cohesion_mQgsDoubleSpinBox.setMaximum(9999.9)
        self.Cohesion_mQgsDoubleSpinBox.setValue(0.0)

        self.FA_mQgsDoubleSpinBox.setMinimum(0.0)
        self.FA_mQgsDoubleSpinBox.setMaximum(360.0)
        self.FA_mQgsDoubleSpinBox.setValue(0.0)

        if hasattr(self, 'Iterasi_500_radioButton'):
            self.Iterasi_500_radioButton.setChecked(True)

    def _setup_layer_filters(self):

        self.DEM_mMapLayerComboBox.setFilters(
            QgsMapLayerProxyModel.RasterLayer
        )
        self.CSL_mMapLayerComboBox.setFilters(
            QgsMapLayerProxyModel.LineLayer
        )

    def _setup_connections(self):
        self.pushButton.clicked.connect(self.on_calculate_clicked)

    # =========================================================================
    # LAYER ATRIBUT QGIS
    # =========================================================================

    def _export_results_to_attribute_layer(self, results_by_name, tab_inputs_by_name,
                                             layer_name="Slope Stability Results Summary"):

        for lyr in QgsProject.instance().mapLayersByName(layer_name):
            QgsProject.instance().removeMapLayer(lyr.id())

        lyr = QgsVectorLayer("None", layer_name, "memory")
        pr = lyr.dataProvider()

        pr.addAttributes([
            QgsField("cross_sect",      QVariant.String),
            QgsField("fs_det",          QVariant.Double),
            QgsField("status",          QVariant.String),
            QgsField("fs_mean",         QVariant.Double),
            QgsField("fs_std",          QVariant.Double),
            QgsField("reliability_idx", QVariant.Double),
            QgsField("pf_percent",      QVariant.Double),
            QgsField("xc",              QVariant.Double),
            QgsField("zc",              QVariant.Double),
            QgsField("radius",          QVariant.Double),
            QgsField("n_valid",         QVariant.Int),
            QgsField("n_mc",            QVariant.Int),
            QgsField("interval",        QVariant.Double),
            QgsField("gamma_mean",      QVariant.Double),
            QgsField("cohesion_mean",   QVariant.Double),
            QgsField("phi_mean",        QVariant.Double),
            QgsField("timestamp",       QVariant.String),
        ])
        lyr.updateFields()

        if not lyr.isValid():
            raise ValueError(
                "Failed to create the summary attribute layer (the memory "
                "provider rejected the given parameters)."
            )

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for name, result in results_by_name.items():
            FS_det = result["FS_det"]
            FS_mean = result["FS_mean"]
            FS_std = result["FS_std"]
            Pf = result["Pf"]
            xc, zc, R = result["best_center"]
            status = self._classify_fs(FS_det)
            n_valid = len(result["all_arcs"])

            reliability_stats = result.get("reliability_stats", {})
            beta = reliability_stats.get("beta")
            reliability_idx = beta if (beta is not None and beta != float('inf')) else None

            t_in = tab_inputs_by_name.get(name, {})
            stats = t_in.get("material_stats", {})
            gamma_mean = stats.get("gamma", {}).get("mean")
            cohesion_mean = stats.get("cohesion", {}).get("mean")
            phi_mean = stats.get("phi", {}).get("mean")

            feat = QgsFeature()
            feat.setFields(lyr.fields())
            feat.setAttributes([
                name,
                round(FS_det, 4),
                status,
                round(FS_mean, 4),
                round(FS_std, 4),
                round(reliability_idx, 4) if reliability_idx is not None else None,
                round(Pf * 100, 3),
                round(xc, 3),
                round(zc, 3),
                round(R, 3),
                n_valid,
                t_in.get("n_mc"),
                t_in.get("interval"),
                round(gamma_mean, 3) if gamma_mean is not None else None,
                round(cohesion_mean, 3) if cohesion_mean is not None else None,
                round(phi_mean, 3) if phi_mean is not None else None,
                now_str,
            ])
            pr.addFeature(feat)

        lyr.updateExtents()
        QgsProject.instance().addMapLayer(lyr)
        return lyr

    # =========================================================================
    # TAB WIDGET: tambah / hapus / tutup tab
    # =========================================================================

    def _setup_tab_widget(self):

        self.tabWidget.setTabsClosable(True)

        self.tabWidget.tabCloseRequested.connect(self._remove_material_tab)

        self.tabWidget.setUsesScrollButtons(True)
        self.tabWidget.setElideMode(Qt.ElideNone)
        self.tabWidget.tabBar().setExpanding(False)

        self._tab_wheel_scroller = _TabBarWheelScroller(self.tabWidget)
        self.tabWidget.tabBar().installEventFilter(self._tab_wheel_scroller)

        self.tabWidget.tabBar().setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabWidget.tabBar().customContextMenuRequested.connect(
            self._show_tab_context_menu
        )

        self._btn_add_tab = QPushButton("+")
        self._btn_add_tab.setFixedSize(24, 24)
        self._btn_add_tab.setToolTip("Add New Material Tab")
        self._btn_add_tab.clicked.connect(self._add_material_tab)
        self.tabWidget.setCornerWidget(self._btn_add_tab, Qt.TopRightCorner)

        self._renumber_tabs()

        for i in range(self.tabWidget.count()):
            self._wire_distribution_std_toggle(self.tabWidget.widget(i))
            self._wire_auto_rel_min_max(self.tabWidget.widget(i))

    @staticmethod
    def _find_by_prefix(parent, widget_type, base_name):

        for child in parent.findChildren(widget_type):
            name = child.objectName()
            if name == base_name or name.startswith(base_name + "_"):
                return child
        return None

    def _wire_distribution_std_toggle(self, tab_widget):

        for prefix in MATERIAL_PROPS:
            dist_combo = self._find_by_prefix(tab_widget, QComboBox, f"{prefix}_Dis_comboBox")
            std_spin = self._find_by_prefix(tab_widget, QgsDoubleSpinBox, f"{prefix}_Std_mQgsDoubleSpinBox")

            if dist_combo is None or std_spin is None:
                continue

            dist_combo.blockSignals(True)
            dist_combo.clear()
            dist_combo.addItems(DISTRIBUSI)
            dist_combo.setCurrentText("Normal")
            dist_combo.blockSignals(False)

            def make_handler(spin):
                def handler(text):
                    spin.setEnabled(text != "Uniform")
                return handler

            handler = make_handler(std_spin)
            dist_combo.currentTextChanged.connect(handler)
            handler(dist_combo.currentText())  # State awal (Normal → enabled)

    def _wire_auto_rel_min_max(self, tab_widget):

        auto_btn = self._find_by_prefix(tab_widget, QPushButton, "Auto_pushButton")
        if auto_btn is None:
            return

        row_widgets = {}
        for prefix in MATERIAL_PROPS:
            mean_spin = self._find_by_prefix(tab_widget, QgsDoubleSpinBox, f"{prefix}_mQgsDoubleSpinBox")
            std_spin = self._find_by_prefix(tab_widget, QgsDoubleSpinBox, f"{prefix}_Std_mQgsDoubleSpinBox")
            min_spin = self._find_by_prefix(tab_widget, QgsDoubleSpinBox, f"{prefix}_Min_mQgsDoubleSpinBox")
            max_spin = self._find_by_prefix(tab_widget, QgsDoubleSpinBox, f"{prefix}_Max_mQgsDoubleSpinBox")

            if None in (mean_spin, std_spin, min_spin, max_spin):
                continue

            row_widgets[prefix] = {
                "mean": mean_spin, "std": std_spin, "min": min_spin, "max": max_spin,
            }

            for w in (min_spin, max_spin):
                w.installEventFilter(self._focus_tracker)
                self._rel_widget_map[w] = (tab_widget, prefix)

        def on_auto_clicked():
            prefix = self._last_focused_prefix.get(tab_widget)
            if prefix is None or prefix not in row_widgets:
                QMessageBox.information(
                    self, "Select a Row First",
                    "Click one of the Rel. Min or Rel. Max fields first\n"
                    "(Unit Weight / Friction Angle / Cohesion), then press "
                    "the Auto button."
                )
                return

            widgets = row_widgets[prefix]
            mean = widgets["mean"].value()
            std = widgets["std"].value()
            widgets["min"].setValue(mean - 3 * std)
            widgets["max"].setValue(mean + 3 * std)

        auto_btn.clicked.connect(on_auto_clicked)

    def _add_material_tab(self):

        new_page = self._build_material_tab_widget()
        index = self.tabWidget.addTab(new_page, "")
        self.tabWidget.setCurrentIndex(index)
        self._renumber_tabs()

    def _remove_material_tab(self, index):

        if self.tabWidget.count() <= 1:
            QMessageBox.information(
                self, "Cannot Delete",
                "At least 1 tab is required."
            )
            return

        widget = self.tabWidget.widget(index)
        self.tabWidget.removeTab(index)
        if widget is not None:
            widget.deleteLater()  # bersihkan widget dari memori

        self._renumber_tabs()

    def _renumber_tabs(self):

        for i in range(self.tabWidget.count()):
            tab_widget = self.tabWidget.widget(i)
            full_name = f"Cross Section {i + 1}"
            self.tabWidget.setTabToolTip(i, full_name)

            if tab_widget.property("is_pinned") is True:
                self.tabWidget.setTabText(i, f"CSL {i +1}")
            else:
                self.tabWidget.setTabText(i, full_name)

    def _show_tab_context_menu(self, pos):

        tab_bar = self.tabWidget.tabBar()
        index = tab_bar.tabAt(pos)
        if index < 0:
            return

        tab_widget = self.tabWidget.widget(index)
        is_pinned = tab_widget.property("is_pinned") is True

        menu = QMenu(self)
        label = "Unpin Tab" if is_pinned else "Pin Tab (Minimize)"
        action = menu.addAction(label)
        action.triggered.connect(lambda: self._toggle_tab_pin(index))
        menu.exec_(tab_bar.mapToGlobal(pos))

    def _toggle_tab_pin(self, index):

        tab_widget = self.tabWidget.widget(index)
        is_pinned = tab_widget.property("is_pinned") is True
        tab_widget.setProperty("is_pinned", not is_pinned)
        self._renumber_tabs()

    def _build_material_tab_widget(self):

        page = QWidget()
        page_layout = QVBoxLayout(page)

        # Groupbox Cross Section Line + Interval
        cs_box = QGroupBox("Surfaces")
        cs_layout = QGridLayout(cs_box)

        cs_layout.addWidget(QLabel("Cross Section Line"), 0, 0)
        csl_combo = QgsMapLayerComboBox()
        csl_combo.setObjectName("CSL_mMapLayerComboBox")
        csl_combo.setFilters(QgsMapLayerProxyModel.LineLayer)
        cs_layout.addWidget(csl_combo, 0, 1)

        cs_layout.addWidget(QLabel("Interval"), 1, 0)
        interval_spin = QgsDoubleSpinBox()
        interval_spin.setObjectName("Interval_mQgsDoubleSpinBox")
        interval_spin.setSuffix(" m")
        interval_spin.setMinimum(0.10)
        interval_spin.setMaximum(10.00)
        interval_spin.setValue(1.00)
        cs_layout.addWidget(interval_spin, 1, 1)

        # Groupbox Material Statistics
        param_box = QGroupBox("Material Statistics")
        grid = QGridLayout(param_box)

        headers = ["Material Property", "Mean", "Distribution", "Std. Dev", "Rel. Min", "Rel. Max"]
        for col, text in enumerate(headers):
            grid.addWidget(QLabel(f"<b>{text}</b>"), 0, col)

        rows = [
            ("Unit Weight (γ)", "kN/m³", "Weight"),
            ("Friction Angle (φ')", "°", "FA"),
            ("Cohesion (c')", "kPa", "Cohesion"),
        ]

        for row_i, (label_text, suffix, prefix) in enumerate(rows, start=1):
            grid.addWidget(QLabel(label_text), row_i, 0)

            mean_spin = QgsDoubleSpinBox()
            mean_spin.setObjectName(f"{prefix}_mQgsDoubleSpinBox")
            mean_spin.setSuffix(f" {suffix}")
            grid.addWidget(mean_spin, row_i, 1)

            dist_combo = QComboBox()
            dist_combo.setObjectName(f"{prefix}_Dis_comboBox")
            dist_combo.addItems(DISTRIBUSI)
            grid.addWidget(dist_combo, row_i, 2)

            std_spin = QgsDoubleSpinBox()
            std_spin.setObjectName(f"{prefix}_Std_mQgsDoubleSpinBox")
            grid.addWidget(std_spin, row_i, 3)

            min_spin = QgsDoubleSpinBox()
            min_spin.setObjectName(f"{prefix}_Min_mQgsDoubleSpinBox")
            grid.addWidget(min_spin, row_i, 4)

            max_spin = QgsDoubleSpinBox()
            max_spin.setObjectName(f"{prefix}_Max_mQgsDoubleSpinBox")
            grid.addWidget(max_spin, row_i, 5)

            # "Uniform" dipilih → Std. Dev baris ini otomatis di-disable,
            dist_combo.currentTextChanged.connect(
                lambda text, spin=std_spin: spin.setEnabled(text != "Uniform")
            )

        auto_btn = QPushButton("Auto Rel. Min/ Rel. Max")
        auto_btn.setObjectName("Auto_pushButton")
        grid.addWidget(auto_btn, len(rows) + 1, 4, 1, 2)

        # Groupbox Iterasi Monte Carlo
        iter_box = QGroupBox("Monte Carlo Iterations")
        iter_layout = QHBoxLayout(iter_box)

        r500 = QRadioButton("500 Iterations")
        r500.setObjectName("Iterasi_500_radioButton")
        r1000 = QRadioButton("1000 Iterations")
        r1000.setObjectName("Iterasi_1000_radioButton")
        r2000 = QRadioButton("2000 Iterations")
        r2000.setObjectName("Iterasi_2000_radioButton")
        r500.setChecked(True)  # default: 500 iterasi terpilih di tab baru

        iter_layout.addWidget(r500)
        iter_layout.addWidget(r1000)
        iter_layout.addWidget(r2000)

        custom_radio = QRadioButton("Custom")
        custom_radio.setObjectName("Iterasi_Custom_radioButton")
        custom_spin = QgsDoubleSpinBox()
        custom_spin.setObjectName("Iterasi_Costum_mQgsDoubleSpinBox")
        custom_spin.setMinimum(1)
        custom_spin.setMaximum(100000)
        custom_spin.setValue(500)
        iter_layout.addWidget(custom_radio)
        iter_layout.addWidget(custom_spin)

        page_layout.addWidget(cs_box)
        page_layout.addWidget(param_box)
        page_layout.addWidget(iter_box)
        page_layout.addStretch()

        self._wire_auto_rel_min_max(page)

        return page

    # ── Helper: baca data SATU TAB (dipakai loop kalkulasi) ─────────────────

    # Prefix UI (Weight/Cohesion/FA) → kunci yang dipahami prossesing.py
    _MATERIAL_KEY_MAP = {"Weight": "gamma", "Cohesion": "cohesion", "FA": "phi"}

    def _get_cross_section_for_tab(self, tab_widget):

        csl_combo = self._find_by_prefix(tab_widget, QgsMapLayerComboBox, "CSL_mMapLayerComboBox")
        interval_spin = self._find_by_prefix(tab_widget, QgsDoubleSpinBox, "Interval_mQgsDoubleSpinBox")

        layer = csl_combo.currentLayer() if csl_combo is not None else None
        interval = interval_spin.value() if interval_spin is not None else 0.0
        return layer, interval

    def _get_material_stats_for_tab(self, tab_widget):

        stats = {}
        for prefix, key in self._MATERIAL_KEY_MAP.items():
            mean_spin = self._find_by_prefix(tab_widget, QgsDoubleSpinBox, f"{prefix}_mQgsDoubleSpinBox")
            dist_combo = self._find_by_prefix(tab_widget, QComboBox, f"{prefix}_Dis_comboBox")
            std_spin = self._find_by_prefix(tab_widget, QgsDoubleSpinBox, f"{prefix}_Std_mQgsDoubleSpinBox")
            min_spin = self._find_by_prefix(tab_widget, QgsDoubleSpinBox, f"{prefix}_Min_mQgsDoubleSpinBox")
            max_spin = self._find_by_prefix(tab_widget, QgsDoubleSpinBox, f"{prefix}_Max_mQgsDoubleSpinBox")

            mean = mean_spin.value() if mean_spin is not None else 0.0
            dist = dist_combo.currentText() if dist_combo is not None else "Normal"
            std = std_spin.value() if std_spin is not None else 0.0
            rel_min = min_spin.value() if min_spin is not None else 0.0
            rel_max = max_spin.value() if max_spin is not None else 0.0

            has_bounds = not (rel_min == 0.0 and rel_max == 0.0)

            stats[key] = {
                "mean": mean,
                "dist": dist,
                "std": std,
                "rel_min": rel_min if has_bounds else None,
                "rel_max": rel_max if has_bounds else None,
            }
        return stats

    def _get_n_mc_for_tab(self, tab_widget):

        custom_radio = self._find_by_prefix(tab_widget, QRadioButton, "Iterasi_Custom_radioButton")
        if custom_radio is not None and custom_radio.isChecked():
            custom_spin = self._find_by_prefix(tab_widget, QgsDoubleSpinBox, "Iterasi_Costum_mQgsDoubleSpinBox")
            if custom_spin is not None:
                return max(1, int(round(custom_spin.value())))
            return 500

        r1000 = self._find_by_prefix(tab_widget, QRadioButton, "Iterasi_1000_radioButton")
        if r1000 is not None and r1000.isChecked():
            return 1000

        r2000 = self._find_by_prefix(tab_widget, QRadioButton, "Iterasi_2000_radioButton")
        if r2000 is not None and r2000.isChecked():
            return 2000

        return 500  # default: 500 (juga fallback kalau radio 500 yang dipilih)

    def _validate_tab_inputs(self, tab_name, dem_layer, cs_layer, interval, material_stats):
        if dem_layer is None:
            return "DEM has not been selected."
        if cs_layer is None:
            return f"{tab_name}: Cross Section Line has not been selected."
        if interval <= 0:
            return f"{tab_name}: Interval must be greater than 0."
        if not list(cs_layer.getFeatures()):
            return f"{tab_name}: Cross Section Line has no features."
        if material_stats["gamma"]["mean"] <= 0:
            return f"{tab_name}: Unit Weight (γ) must be greater than 0."
        if material_stats["phi"]["mean"] <= 0:
            return f"{tab_name}: Friction Angle (φ') must be greater than 0."
        return None

    # ── Progress (gabungan semua cross section) ──────────────────────────────

    def _make_progress_callback(self, progress_dlg, n_grid, n_mc, base_value, label_prefix):

        def callback(stage, current, _tot, message):
            if progress_dlg.wasCanceled():
                raise InterruptedError("Analysis canceled by user.")
            if stage == 'profile':
                val = current
            elif stage == 'grid':
                val = 1 + current
            else:
                val = 1 + n_grid + current
            progress_dlg.setValue(base_value + val)
            progress_dlg.setLabelText(f"{label_prefix}: {message}")
            QApplication.processEvents()

        return callback

    # ── Gambar ke canvas QGIS ─────────────────────────────────────────────────

    def _draw_results_on_canvas(self, result, crs_authid, suffix=""):
        xs = result["xs"]
        zs = result["zs"]
        arc_x, arc_z = result["best_arc"]
        xc, zc, R = result["best_center"]
        FS_det = result["FS_det"]

        names = {
            "profile": f"Ground Profile{suffix}",
            "arc": f"Slip Surface{suffix}",
            "center": f"Slip Center{suffix}",
        }
        for name in names.values():
            for lyr in QgsProject.instance().mapLayersByName(name):
                QgsProject.instance().removeMapLayer(lyr.id())

        profile_lyr = self._make_line_layer(
            names["profile"],
            [QgsPointXY(float(x), float(z)) for x, z in zip(xs, zs)],
            crs_authid, "#808080", 0.5, False
        )

        arc_lyr = self._make_line_layer(
            names["arc"],
            [QgsPointXY(float(x), float(z)) for x, z in zip(arc_x, arc_z)],
            crs_authid, "#e03030", 1.0, True
        )

        center_lyr = self._make_point_layer(
            names["center"], xc, zc, crs_authid, R, FS_det
        )

        for lyr in (profile_lyr, arc_lyr, center_lyr):
            QgsProject.instance().addMapLayer(lyr)

    def _make_line_layer(self, name, points, crs, color, width, dash):
        lyr = QgsVectorLayer(f"LineString?crs={crs}", name, "memory")
        pr = lyr.dataProvider()
        feat = QgsFeature()
        feat.setGeometry(QgsGeometry.fromPolylineXY(points))
        pr.addFeature(feat)
        lyr.updateExtents()
        sym = QgsLineSymbol.createSimple({
            "color": color,
            "width": str(width),
            "line_style": "dash" if dash else "solid",
        })
        lyr.setRenderer(QgsSingleSymbolRenderer(sym))
        return lyr

    def _make_point_layer(self, name, x, z, crs, R, FS_det):
        lyr = QgsVectorLayer(f"Point?crs={crs}", name, "memory")
        pr = lyr.dataProvider()
        pr.addAttributes([
            QgsField("xc", QVariant.Double),
            QgsField("zc", QVariant.Double),
            QgsField("R_m", QVariant.Double),
            QgsField("FS", QVariant.Double),
        ])
        lyr.updateFields()
        feat = QgsFeature()
        feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, z)))
        feat.setAttributes([round(x, 3), round(z, 3),
                            round(R, 3), round(FS_det, 4)])
        pr.addFeature(feat)
        lyr.updateExtents()
        sym = QgsMarkerSymbol.createSimple({
            "color": "#1a6bbf",
            "size": "4",
            "outline_color": "#0a3d70",
            "outline_width": "0.4",
        })
        lyr.setRenderer(QgsSingleSymbolRenderer(sym))
        return lyr

    # ── Klasifikasi ────────────────────────────────────────────────────────────

    @staticmethod
    def _classify_fs(fs):
        if fs >= 1.50:
            return "VERY STABLE ✓"
        if fs >= 1.25:
            return "STABLE"
        return "CRITICAL / UNSTABLE ✗"

    # SLOT UTAMA

    def on_calculate_clicked(self):

        # 1. DEM (global)
        dem_layer = self.DEM_mMapLayerComboBox.currentLayer()
        if dem_layer is None:
            QMessageBox.warning(self, "Invalid Input", "DEM has not been selected.")
            return

        n_tabs = self.tabWidget.count()
        if n_tabs == 0:
            QMessageBox.warning(self, "Invalid Input", "No cross sections available.")
            return

        # 2–3. Kumpulkan & validasi semua tab dulu, sebelum proses berat dimulai
        tab_inputs = []
        for i in range(n_tabs):
            tab_widget = self.tabWidget.widget(i)
            tab_name = f"Cross Section {i + 1}"

            cs_layer, interval = self._get_cross_section_for_tab(tab_widget)
            material_stats = self._get_material_stats_for_tab(tab_widget)
            n_mc = self._get_n_mc_for_tab(tab_widget)

            err = self._validate_tab_inputs(tab_name, dem_layer, cs_layer, interval, material_stats)
            if err:
                QMessageBox.warning(self, "Invalid Input", err)
                return

            tab_inputs.append({
                "name": tab_name,
                "layer": cs_layer,
                "interval": interval,
                "material_stats": material_stats,
                "n_mc": n_mc,
            })

        crs_authid = dem_layer.crs().authid()

        # 4. Progress dialog gabungan
        n_grid = 20 * 20 * 10  # 20×20 centers, 10 R per center
        grand_total = sum(1 + n_grid + t["n_mc"] for t in tab_inputs)

        prog = QProgressDialog("Starting analysis...", "Cancel", 0, grand_total, self)
        prog.setWindowTitle("Slope Stability Analysis – Multi Cross Section")
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        prog.setValue(0)

        results_by_name = {}
        figures_by_name = {}
        errors_by_name = {}
        base_value = 0

        # 5. Loop tiap cross section
        for t in tab_inputs:
            name = t["name"]
            cs_feature = list(t["layer"].getFeatures())[0]

            cb = self._make_progress_callback(prog, n_grid, t["n_mc"], base_value, label_prefix=name)

            try:
                result = run_full_analysis(
                    dem_layer=dem_layer,
                    line_feature=cs_feature,
                    interval=t["interval"],
                    material_stats=t["material_stats"],
                    n_mc=t["n_mc"],
                    n_grid_cx=20,
                    n_grid_cy=20,
                    n_grid_r=10,
                    progress_callback=cb,
                )
                results_by_name[name] = result

                fig = plot_results(result, material_stats=t["material_stats"])
                figures_by_name[name] = fig

            except InterruptedError:
                prog.close()
                QMessageBox.information(self, "Canceled", "Analysis was canceled by the user.")
                return
            except Exception as e:
                errors_by_name[name] = str(e)

            base_value += 1 + n_grid + t["n_mc"]

        prog.setValue(prog.maximum())
        prog.close()

        if not results_by_name:
            QMessageBox.critical(
                self, "Processing Error",
                "All cross sections failed to analyze:\n\n" +
                "\n".join(f"• {n}: {e}" for n, e in errors_by_name.items())
            )
            return

        # Simpan hasil (dict multi-tab), plus _last_result (hasil tab pertama
        # saja) sebagai state cadangan kalau nanti dibutuhkan lagi di tempat lain.
        self._last_results = results_by_name
        self._last_result = next(iter(results_by_name.values()))
        self._last_crs = crs_authid

        # 6. Ekspor ringkasan hasil ke layer atribut QGIS (bisa dibuka lewat
        # Attribute Table, di-export CSV, dan ikut tersimpan di project).
        tab_inputs_by_name = {t["name"]: t for t in tab_inputs}
        try:
            self._export_results_to_attribute_layer(results_by_name, tab_inputs_by_name)
        except Exception as e:
            QMessageBox.warning(
                self, "Warning",
                f"Results are valid, but failed to create the summary attribute layer:\n{e}"
            )

        # 7. Plot matplotlib multi-tab
        try:
            if self._plot_dlg is not None:
                try:
                    self._plot_dlg.close()
                    for fig in self._plot_dlg._figures_by_name.values():
                        plt.close(fig)
                except Exception:
                    pass

            self._plot_dlg = MultiPlotDialog(figures_by_name, parent=self)
            self._plot_dlg.show()

        except Exception as e:
            QMessageBox.warning(self, "Plot Warning", f"Unable to create the plot:\n{e}")

        # 8. Layer ke canvas QGIS (per cross section, nama diberi suffix)
        for name, result in results_by_name.items():
            try:
                self._draw_results_on_canvas(result, crs_authid, suffix=f" - {name}")
            except Exception as e:
                QMessageBox.warning(
                    self, "Visualization Warning",
                    f"{name}: results are valid, but failed to draw the layer:\n{e}"
                )

        # 9. Ringkasan gabungan
        lines = [
            "══════════════════════════════════════",
            "   SLOPE STABILITY ANALYSIS RESULTS",
            f"   ({len(results_by_name)} Cross Section(s))",
            "══════════════════════════════════════",
            "",
        ]

        for name, result in results_by_name.items():
            FS_det = result["FS_det"]
            FS_mean = result["FS_mean"]
            FS_std = result["FS_std"]
            Pf = result["Pf"]
            xc, zc, R = result["best_center"]
            status = self._classify_fs(FS_det)

            lines.append(f"── {name} ─────────────────────")
            lines.append(f"  Min. FoS (det.)    = {FS_det:.3f}  [{status}]")
            lines.append(f"  Mean FoS (MC)      = {FS_mean:.3f} ± {FS_std:.3f}")
            lines.append(f"  P(failure)         = {Pf * 100:.2f} %")
            lines.append(f"  Slip center        = ({xc:.2f}, {zc:.2f}) m, R = {R:.2f} m")
            lines.append(f"  Valid candidates   = {len(result['all_arcs'])}")
            lines.append("")

        if errors_by_name:
            lines.append("⚠ Cross sections that failed to analyze:")
            for name, e in errors_by_name.items():
                lines.append(f"  • {name}: {e}")
            lines.append("")

        lines.append("Plot, QGIS layers, and the summary attribute layer have been added.")
        msg = "\n".join(lines)

        msgBox = QMessageBox(self)
        msgBox.setWindowTitle("Analysis Results")
        msgBox.setText(msg)
        msgBox.setIcon(QMessageBox.Information)
        msgBox.addButton("Close", QMessageBox.AcceptRole)
        msgBox.exec_()