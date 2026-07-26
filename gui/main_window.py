import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QTextCursor, QColor
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QCheckBox, QPushButton, QProgressBar, QPlainTextEdit, QSplitter,
    QListWidget, QListWidgetItem, QStatusBar, QMessageBox, QToolBar, QGridLayout
)

from core.config import AppConfig
from core.database import Database
from utils.logger import setup_logging, get_emitter
from gui.settings_dialog import SettingsDialog
from gui.worker import ScrapeWorker, ScrapeControl


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Fouani Store -> WooCommerce Product Scraper")
        self.resize(1150, 800)

        self.config = AppConfig.load()
        self.config.ensure_dirs()
        self.logger = setup_logging(self.config.logs_folder)
        self.db = Database(self.config.db_path)
        self.control = ScrapeControl()
        self.worker = None

        self._build_menu()
        self._build_toolbar()
        self._build_central()
        self._build_statusbar()

        emitter = get_emitter()
        if emitter:
            emitter.new_record.connect(self._append_log)

        self._refresh_queue_counts()
        self.logger.info("Application started.")

    # ---------------- UI construction ----------------

    def _build_menu(self):
        menu = self.menuBar()
        file_menu = menu.addMenu("&File")

        settings_action = QAction("Settings...", self)
        settings_action.triggered.connect(self._open_settings)
        file_menu.addAction(settings_action)

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        help_menu = menu.addMenu("&Help")
        about_action = QAction("About", self)
        about_action.triggered.connect(lambda: QMessageBox.information(
            self, "About",
            "Fouani Store -> WooCommerce Product Scraper\n"
            "Scrapes fouanistore.com/nigeria-en and exports WooCommerce-ready "
            "product data (CSV / JSON / SQLite) with optional direct WooCommerce sync."
        ))
        help_menu.addAction(about_action)

    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)

        self.scan_btn = QPushButton("Scan Website")
        self.start_btn = QPushButton("Start Scraping")
        self.pause_btn = QPushButton("Pause")
        self.resume_btn = QPushButton("Resume")
        self.stop_btn = QPushButton("Stop")
        self.export_btn = QPushButton("Export")
        self.settings_btn = QPushButton("Settings")

        for b in (self.scan_btn, self.start_btn, self.pause_btn, self.resume_btn,
                  self.stop_btn, self.export_btn, self.settings_btn):
            tb.addWidget(b)

        self.scan_btn.clicked.connect(lambda: self._start_run(mode="scan"))
        self.start_btn.clicked.connect(lambda: self._start_run(mode="full"))
        self.pause_btn.clicked.connect(self._pause)
        self.resume_btn.clicked.connect(self._resume)
        self.stop_btn.clicked.connect(self._stop)
        self.export_btn.clicked.connect(self._export_only)
        self.settings_btn.clicked.connect(self._open_settings)

        self._set_running_state(False)

    def _build_central(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)

        # -- website info --
        info_box = QGroupBox("Website Information")
        info_grid = QGridLayout(info_box)
        self.base_url_label = QLabel(self.config.base_url)
        self.conn_status_label = QLabel("Idle")
        self.products_found_label = QLabel("0")
        self.current_page_label = QLabel("-")
        self.current_category_label = QLabel("-")
        info_grid.addWidget(QLabel("Base URL:"), 0, 0)
        info_grid.addWidget(self.base_url_label, 0, 1)
        info_grid.addWidget(QLabel("Connection Status:"), 0, 2)
        info_grid.addWidget(self.conn_status_label, 0, 3)
        info_grid.addWidget(QLabel("Products Found:"), 1, 0)
        info_grid.addWidget(self.products_found_label, 1, 1)
        info_grid.addWidget(QLabel("Current Category:"), 1, 2)
        info_grid.addWidget(self.current_category_label, 1, 3)
        outer.addWidget(info_box)

        top_split = QHBoxLayout()

        # -- category selection --
        cat_box = QGroupBox("Category Selection")
        cat_layout = QVBoxLayout(cat_box)
        self.category_list = QListWidget()
        self.all_categories_check = QCheckBox("All Categories")
        self.all_categories_check.stateChanged.connect(self._toggle_all_categories)
        cat_layout.addWidget(self.all_categories_check)
        cat_layout.addWidget(self.category_list)
        for cat in self.config.categories:
            item = QListWidgetItem(cat["name"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            item.setData(Qt.UserRole, cat)
            self.category_list.addItem(item)
        top_split.addWidget(cat_box, 2)

        # -- export options --
        export_box = QGroupBox("Export Options")
        export_layout = QVBoxLayout(export_box)
        self.csv_check = QCheckBox("WooCommerce CSV")
        self.csv_check.setChecked(self.config.export_csv)
        self.json_check = QCheckBox("JSON")
        self.json_check.setChecked(self.config.export_json)
        self.sqlite_check = QCheckBox("SQLite")
        self.sqlite_check.setChecked(self.config.export_sqlite)
        self.images_check = QCheckBox("Download Images")
        self.images_check.setChecked(self.config.download_images)
        self.wc_sync_check = QCheckBox("WooCommerce Sync")
        self.wc_sync_check.setChecked(self.config.woocommerce_sync)
        for cb in (self.csv_check, self.json_check, self.sqlite_check,
                   self.images_check, self.wc_sync_check):
            export_layout.addWidget(cb)
        export_layout.addStretch()
        top_split.addWidget(export_box, 1)

        outer.addLayout(top_split)

        # -- progress --
        progress_box = QGroupBox("Progress")
        pg = QGridLayout(progress_box)
        self.found_label = QLabel("0")
        self.processed_label = QLabel("0")
        self.images_label = QLabel("0")
        self.synced_label = QLabel("0")
        self.errors_label = QLabel("0")
        self.elapsed_label = QLabel("00:00:00")
        self.eta_label = QLabel("-")
        pg.addWidget(QLabel("Products Found:"), 0, 0); pg.addWidget(self.found_label, 0, 1)
        pg.addWidget(QLabel("Products Processed:"), 0, 2); pg.addWidget(self.processed_label, 0, 3)
        pg.addWidget(QLabel("Images Downloaded:"), 1, 0); pg.addWidget(self.images_label, 1, 1)
        pg.addWidget(QLabel("Errors:"), 1, 2); pg.addWidget(self.errors_label, 1, 3)
        pg.addWidget(QLabel("Products Synced:"), 2, 0); pg.addWidget(self.synced_label, 2, 1)
        pg.addWidget(QLabel("Elapsed Time:"), 3, 0); pg.addWidget(self.elapsed_label, 3, 1)
        pg.addWidget(QLabel("ETA:"), 3, 2); pg.addWidget(self.eta_label, 3, 3)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        pg.addWidget(self.progress_bar, 4, 0, 1, 4)
        outer.addWidget(progress_box)

        # -- log window --
        log_box = QGroupBox("Log Window")
        log_layout = QVBoxLayout(log_box)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        log_layout.addWidget(self.log_view)
        outer.addWidget(log_box, 1)

        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)
        self._run_start_time = None

    def _build_statusbar(self):
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Ready")

    # ---------------- helpers ----------------

    def _toggle_all_categories(self, state):
        is_checked = state == 2 or state == Qt.Checked
        checked = Qt.Checked if is_checked else Qt.Unchecked
        for i in range(self.category_list.count()):
            self.category_list.item(i).setCheckState(checked)

    def _selected_categories(self):
        selected = []
        for i in range(self.category_list.count()):
            item = self.category_list.item(i)
            if item.checkState() == Qt.Checked:
                selected.append(item.data(Qt.UserRole))
        return selected

    def _set_running_state(self, running: bool):
        self.scan_btn.setEnabled(not running)
        self.start_btn.setEnabled(not running)
        self.pause_btn.setEnabled(running)
        self.resume_btn.setEnabled(False)
        self.stop_btn.setEnabled(running)
        self.export_btn.setEnabled(not running and self.db.product_count() > 0)

    def _append_log(self, level, message):
        color = {
            "DEBUG": "#888888", "INFO": "#1a1a1a", "WARNING": "#b8860b",
            "ERROR": "#c0392b", "CRITICAL": "#c0392b",
        }.get(level, "#1a1a1a")
        self.log_view.appendHtml(f'<span style="color:{color}">{message}</span>')
        self.log_view.moveCursor(QTextCursor.End)

    def _refresh_queue_counts(self):
        counts = self.db.queue_counts()
        total = sum(counts.values())
        self.products_found_label.setText(str(total))
        self.found_label.setText(str(total))

    # ---------------- run control ----------------

    def _apply_export_options_to_config(self):
        c = self.config
        c.export_csv = self.csv_check.isChecked()
        c.export_json = self.json_check.isChecked()
        c.export_sqlite = self.sqlite_check.isChecked()
        c.download_images = self.images_check.isChecked()
        c.woocommerce_sync = self.wc_sync_check.isChecked()
        c.save()

    def _start_run(self, mode):
        categories = self._selected_categories()
        if not categories:
            QMessageBox.warning(self, "No categories selected",
                                 "Please select at least one category (or check 'All Categories').")
            return

        self._apply_export_options_to_config()
        self.control.reset()

        self.worker = ScrapeWorker(self.config, self.db, self.logger, self.control,
                                    categories, mode=mode)
        self.worker.log.connect(self._append_log)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)

        self._run_start_time = time.time()
        self._elapsed_timer.start(1000)
        self.conn_status_label.setText("Running")
        self.status.showMessage("Scraping in progress...")
        self._set_running_state(True)
        self.worker.start()

    def _pause(self):
        self.control.pause()
        self.pause_btn.setEnabled(False)
        self.resume_btn.setEnabled(True)
        self.conn_status_label.setText("Paused")
        self.status.showMessage("Paused")

    def _resume(self):
        self.control.resume()
        self.pause_btn.setEnabled(True)
        self.resume_btn.setEnabled(False)
        self.conn_status_label.setText("Running")
        self.status.showMessage("Scraping in progress...")

    def _stop(self):
        self.control.stop()
        self.status.showMessage("Stopping...")

    def _on_progress(self, counters):
        self.found_label.setText(str(counters.get("products_found", 0)))
        self.processed_label.setText(str(counters.get("products_processed", 0)))
        self.images_label.setText(str(counters.get("images_downloaded", 0)))
        self.synced_label.setText(str(counters.get("products_synced", 0)))
        self.errors_label.setText(str(counters.get("errors", 0)))

        total_products = counters.get("products_found", 0)
        if total_products == 0:
            return

        # Calculate total work: 1 unit for scraping, 1 for syncing (if enabled)
        work_per_product = 1
        if self.config.woocommerce_sync:
            work_per_product += 1

        total_work = total_products * work_per_product
        completed_work = counters.get("products_processed", 0) + counters.get("products_synced", 0)

        if total_work > 0:
            pct = min(100, int(completed_work / total_work * 100))
            self.progress_bar.setValue(pct)
            elapsed = time.time() - (self._run_start_time or time.time())
            if completed_work > 0:
                per_unit = elapsed / completed_work
                remaining = per_unit * max(0, total_work - completed_work)
                self.eta_label.setText(time.strftime("%H:%M:%S", time.gmtime(remaining)))

    def _tick_elapsed(self):
        if self._run_start_time:
            elapsed = time.time() - self._run_start_time
            self.elapsed_label.setText(time.strftime("%H:%M:%S", time.gmtime(elapsed)))

    def _on_finished(self, summary):
        self._elapsed_timer.stop()
        self._set_running_state(False)
        self.conn_status_label.setText("Finished")
        mode = summary.get("mode")
        if mode == "scan":
            self.status.showMessage("Scan complete.")
            QMessageBox.information(self, "Scan complete",
                                     f"Discovery finished. {summary.get('products_found', 0)} product URLs found.")
        elif mode == "stopped":
            self.status.showMessage("Stopped by user.")
        else:
            self.status.showMessage("Scraping complete.")
            msg = (f"Processed: {summary.get('products_processed', 0)}\n"
                   f"Images downloaded: {summary.get('images_downloaded', 0)}\n"
                   f"Errors: {summary.get('errors', 0)}\n")
            if summary.get("csv_path"):
                msg += f"\nCSV: {summary['csv_path']}"
            if summary.get("json_path"):
                msg += f"\nJSON: {summary['json_path']}"
            QMessageBox.information(self, "Scraping complete", msg)
        self._refresh_queue_counts()

    def _on_failed(self, error_message):
        self._elapsed_timer.stop()
        self._set_running_state(False)
        self.conn_status_label.setText("Error")
        self.status.showMessage("Failed.")
        QMessageBox.critical(self, "Scraping failed", error_message)

    def _export_only(self):
        from core.exporter import export_csv, export_json
        products = self.db.all_products()
        if not products:
            QMessageBox.information(self, "Nothing to export", "No products in the database yet.")
            return
        paths = []
        if self.csv_check.isChecked():
            paths.append(export_csv(products, self.config.csv_folder))
        if self.json_check.isChecked():
            paths.append(export_json(products, self.config.json_folder))
        QMessageBox.information(self, "Export complete", "\n".join(paths) if paths else "Nothing selected to export.")

    def _open_settings(self):
        dlg = SettingsDialog(self.config, self)
        if dlg.exec():
            self.logger.info("Settings saved.")

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            reply = QMessageBox.question(self, "Scraping in progress",
                                          "A scrape is still running. Stop and exit?",
                                          QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            self.control.stop()
            self.worker.wait(5000)
        self.db.close()
        event.accept()
