from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QTabWidget, QWidget, QSpinBox,
    QLineEdit, QCheckBox, QPushButton, QHBoxLayout, QFileDialog, QLabel, QMessageBox
)


class SettingsDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Settings")
        self.resize(520, 480)

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        tabs.addTab(self._build_general_tab(), "General")
        tabs.addTab(self._build_images_tab(), "Images")
        tabs.addTab(self._build_woocommerce_tab(), "WooCommerce")
        tabs.addTab(self._build_export_tab(), "Export")

        btn_row = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _build_general_tab(self):
        w = QWidget()
        form = QFormLayout(w)

        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(1, 32)
        self.threads_spin.setValue(self.config.threads)
        form.addRow("Threads:", self.threads_spin)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 300)
        self.timeout_spin.setValue(self.config.timeout_seconds)
        form.addRow("Timeout (seconds):", self.timeout_spin)

        self.retry_spin = QSpinBox()
        self.retry_spin.setRange(0, 10)
        self.retry_spin.setValue(self.config.retry_count)
        form.addRow("Retry count:", self.retry_spin)

        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 10000)
        self.delay_spin.setValue(self.config.delay_between_requests_ms)
        form.addRow("Delay between requests (ms):", self.delay_spin)

        self.user_agent_edit = QLineEdit(self.config.user_agent)
        form.addRow("User agent:", self.user_agent_edit)

        self.headless_check = QCheckBox("Run browser headless")
        self.headless_check.setChecked(self.config.headless_browser)
        form.addRow(self.headless_check)

        return w

    def _build_images_tab(self):
        w = QWidget()
        form = QFormLayout(w)

        self.download_images_check = QCheckBox("Download images")
        self.download_images_check.setChecked(self.config.download_images)
        form.addRow(self.download_images_check)

        self.max_res_spin = QSpinBox()
        self.max_res_spin.setRange(0, 10000)
        self.max_res_spin.setValue(self.config.max_image_resolution)
        self.max_res_spin.setSpecialValueText("Original (no resize)")
        form.addRow("Max resolution (px, 0=original):", self.max_res_spin)

        self.skip_existing_check = QCheckBox("Skip existing images")
        self.skip_existing_check.setChecked(self.config.skip_existing_images)
        form.addRow(self.skip_existing_check)

        return w

    def _build_woocommerce_tab(self):
        w = QWidget()
        form = QFormLayout(w)

        self.wc_url_edit = QLineEdit(self.config.woocommerce.store_url)
        self.wc_url_edit.setPlaceholderText("https://yourstore.com")
        form.addRow("Store URL:", self.wc_url_edit)

        self.wc_key_edit = QLineEdit(self.config.woocommerce.consumer_key)
        form.addRow("Consumer Key:", self.wc_key_edit)

        self.wc_secret_edit = QLineEdit(self.config.woocommerce.consumer_secret)
        self.wc_secret_edit.setEchoMode(QLineEdit.Password)
        form.addRow("Consumer Secret:", self.wc_secret_edit)

        self.wc_verify_ssl_check = QCheckBox("Verify SSL certificate (turn off for local dev sites)")
        self.wc_verify_ssl_check.setChecked(self.config.woocommerce.verify_ssl)
        form.addRow(self.wc_verify_ssl_check)

        hint = QLabel(
            "Tip: for a local WordPress test site, use the plain http:// URL "
            "(e.g. http://localhost:10004) - the app automatically switches to "
            "query-string auth for http:// stores since WooCommerce's Basic Auth "
            "needs HTTPS."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666; font-size: 11px;")
        form.addRow(hint)

        test_btn = QPushButton("Test Connection")
        test_btn.clicked.connect(self._test_wc_connection)
        form.addRow(test_btn)

        return w

    def _build_export_tab(self):
        w = QWidget()
        form = QFormLayout(w)

        self.csv_folder_edit, csv_row = self._folder_row(self.config.csv_folder)
        form.addRow("CSV folder:", csv_row)

        self.json_folder_edit, json_row = self._folder_row(self.config.json_folder)
        form.addRow("JSON folder:", json_row)

        self.images_folder_edit, images_row = self._folder_row(self.config.images_folder)
        form.addRow("Image folder:", images_row)

        self.db_path_edit = QLineEdit(self.config.db_path)
        form.addRow("Database path:", self.db_path_edit)

        return w

    def _folder_row(self, initial_value):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit(initial_value)
        browse = QPushButton("Browse...")

        def pick():
            d = QFileDialog.getExistingDirectory(self, "Choose folder", edit.text())
            if d:
                edit.setText(d)

        browse.clicked.connect(pick)
        layout.addWidget(edit)
        layout.addWidget(browse)
        return edit, row

    def _test_wc_connection(self):
        from core.woocommerce import WooCommerceClient
        url, key, secret = self.wc_url_edit.text().strip(), self.wc_key_edit.text().strip(), self.wc_secret_edit.text().strip()
        if not (url and key and secret):
            QMessageBox.warning(self, "Missing info", "Please fill in Store URL, Consumer Key and Consumer Secret.")
            return
        client = WooCommerceClient(url, key, secret, verify_ssl=self.wc_verify_ssl_check.isChecked())
        ok, msg = client.test_connection()
        if ok:
            QMessageBox.information(self, "Connection successful", msg)
        else:
            QMessageBox.critical(self, "Connection failed", msg)

    def _save(self):
        c = self.config
        c.threads = self.threads_spin.value()
        c.timeout_seconds = self.timeout_spin.value()
        c.retry_count = self.retry_spin.value()
        c.delay_between_requests_ms = self.delay_spin.value()
        c.user_agent = self.user_agent_edit.text()
        c.headless_browser = self.headless_check.isChecked()

        c.download_images = self.download_images_check.isChecked()
        c.max_image_resolution = self.max_res_spin.value()
        c.skip_existing_images = self.skip_existing_check.isChecked()

        c.woocommerce.store_url = self.wc_url_edit.text().strip()
        c.woocommerce.consumer_key = self.wc_key_edit.text().strip()
        c.woocommerce.consumer_secret = self.wc_secret_edit.text().strip()
        c.woocommerce.verify_ssl = self.wc_verify_ssl_check.isChecked()

        c.csv_folder = self.csv_folder_edit.text().strip()
        c.json_folder = self.json_folder_edit.text().strip()
        c.images_folder = self.images_folder_edit.text().strip()
        c.db_path = self.db_path_edit.text().strip()

        c.save()
        self.accept()
