import copy
import logging
from logging.handlers import RotatingFileHandler
import threading
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QSize, Qt, QStandardPaths, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPixmap, QTextCursor, QTextFormat
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog,
    QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSpinBox, QStackedWidget, QTextEdit,
    QVBoxLayout, QWidget,
)

from .captions import blocks
from .motion import MODES
from .images import duplicate, fingerprints, generic_graphic, marked, normalize, placeholder
from .pipeline import Interrupted, existing_image, export, missing_images, process, retry_image, validate_secondary
from .research import CATEGORIES, session
from .script import (MAX_HOOK, SCENE_COUNT, STRICT_RANGE, approve_manual, estimated_seconds,
                     invalid_manual_lines, manual_lines, scene_counts, scene_issues, validate_scenes,
                     word_range, words)
from .settings import defaults
from .store import Store
from .tools import binary
from .voice import VOICES

APP = "Curiosity AI Studio Free"
STYLE = """
QWidget { background: #101621; color: #e9eff8; font-size: 14px; }
QFrame#sidebar { background: #0b101a; border-right: 1px solid #242f43; }
QFrame#sidebar QWidget { background: #0b101a; }
QLabel#brand { color: #61e3cd; font-weight: 800; font-size: 22px; }
QLabel#heading { font-size: 28px; font-weight: 700; }
QLabel#muted { color: #a0b0c7; }
QLineEdit, QPlainTextEdit, QListWidget, QComboBox { background: #171f2d; border: 1px solid #35435b;
    border-radius: 8px; padding: 9px; selection-background-color: #24534f; }
QLineEdit:focus, QPlainTextEdit:focus, QListWidget:focus, QComboBox:focus { border-color: #61e3cd; }
QListWidget::item { padding: 8px; border-radius: 6px; }
QListWidget::item:selected { background: #244948; color: #e9fffb; }
QPushButton { background: #253248; border: 1px solid #40516e; border-radius: 8px;
    padding: 9px 14px; font-weight: 600; }
QPushButton:hover { background: #344561; }
QPushButton#primary { background: #61e3cd; color: #102222; border-color: #61e3cd; }
QPushButton#primary:hover { background: #a1f3e5; }
QPushButton:disabled { background: #192130; color: #8794a8; border-color: #303a4d; }
QProgressBar { border: 0; background: #26334a; border-radius: 4px; max-height: 8px; }
QProgressBar::chunk { background: #61e3cd; border-radius: 4px; }
"""

class Task(QObject):
    progress = Signal(int, str)
    done = Signal(str, str)

    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.stop = threading.Event()

    def check(self):
        if self.stop.is_set():
            raise Interrupted()

    @Slot()
    def run(self):
        try:
            message = self.operation(self.progress.emit, self.check)
            self.done.emit("ok", message or "")
        except Interrupted:
            self.done.emit("stop", "Processamento interrompido. O projeto foi salvo.")
        except Exception as error:
            self.done.emit("error", str(error) or type(error).__name__)

class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP)
        self.resize(1140, 790)
        self.setMinimumSize(840, 640)
        self.settings = QSettings()
        root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
        self.store = Store(root)
        self.project = None
        self.thread = None
        self.task = None
        self.work_id = None
        self.close_pending = False

        central = QWidget()
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.setCentralWidget(central)
        sidebar = QFrame(objectName="sidebar")
        sidebar.setFixedWidth(216)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 26, 18, 20)
        side.addWidget(QLabel("Curiosity AI", objectName="brand"))
        side.addWidget(QLabel("Studio Free", objectName="muted"))
        side.addSpacing(25)
        self.nav = QListWidget()
        self.nav.setAccessibleName("Navegação")
        self.nav.addItems(["Dashboard", "Roteiro", "Galeria", "Histórico", "Configurações"])
        side.addWidget(self.nav, 1)
        self.dependencies = QLabel("Dependências: verificando…", objectName="muted")
        self.dependencies.setWordWrap(True)
        side.addWidget(self.dependencies)
        outer.addWidget(sidebar)
        main = QVBoxLayout()
        main.setContentsMargins(28, 24, 28, 20)
        main.setSpacing(10)
        outer.addLayout(main, 1)
        self.pages = QStackedWidget()
        main.addWidget(self.pages, 1)
        self.status = QLabel("Pronto", objectName="muted")
        main.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        main.addWidget(self.progress)
        self.dashboard()
        self.editor()
        self.gallery()
        self.history_page()
        self.settings_page()
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)
        self.refresh()
        self.check_dependencies()

    def page(self, name):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(QLabel(name, objectName="heading"))
        self.pages.addWidget(widget)
        return layout

    def button(self, label, action, parent, primary=False):
        button = QPushButton(label)
        if primary:
            button.setObjectName("primary")
        button.clicked.connect(action)
        parent.addWidget(button)
        return button

    def dashboard(self):
        page = self.page("Dashboard")
        page.addWidget(QLabel("Vídeo vertical · 70 s · 1080 × 1920 · 30 FPS", objectName="muted"))
        form = QFormLayout()
        self.creation_mode = QComboBox()
        self.creation_mode.addItems(["Gerar roteiro automaticamente com Ollama", "Inserir roteiro manualmente"])
        form.addRow("Criação", self.creation_mode)
        self.category = QComboBox()
        self.category.addItems(CATEGORIES)
        self.topic = QLineEdit(placeholderText="Deixe em branco para escolher automaticamente")
        self.topic.setMaxLength(200)
        self.voice = QComboBox()
        for label, value in VOICES.items():
            self.voice.addItem(label, value)
        self.voice.currentIndexChanged.connect(
            lambda: self.settings.setValue("voice", self.voice.currentData()))
        form.addRow("Categoria", self.category)
        self.category_label = form.labelForField(self.category)
        form.addRow("Assunto personalizado", self.topic)
        form.addRow("Voz", self.voice)
        self.edit_mode = QComboBox()
        self.edit_mode.addItems(MODES)
        self.edit_mode.setCurrentText(self.settings.value("edit_mode", "Leve"))
        self.edit_mode.currentTextChanged.connect(lambda value: self.settings.setValue("edit_mode", value))
        self.intensity = QSpinBox()
        self.intensity.setRange(0, 100)
        self.intensity.setSuffix("%")
        self.intensity.setValue(self.settings.value("intensity", 60, type=int))
        self.intensity.valueChanged.connect(lambda value: self.settings.setValue("intensity", value))
        self.auto_approved = QCheckBox("Buscar imagens após aprovar o roteiro")
        self.auto_approved.setChecked(self.settings.value("auto_approved", True, type=bool))
        self.auto_approved.toggled.connect(lambda value: self.settings.setValue("auto_approved", value))
        editing = QHBoxLayout()
        editing.addWidget(self.edit_mode, 1)
        editing.addWidget(QLabel("Intensidade"))
        editing.addWidget(self.intensity)
        form.addRow("Modo de edição", editing)
        form.addRow(self.auto_approved)
        page.addLayout(form)
        self.automatic = self.button("Gerar roteiro e preparar cenas", lambda: self.start_new("Vídeo"), page, True)
        self.script_only = self.button("Criar roteiro para revisar", lambda: self.start_new("Roteiro"), page)
        self.manual_panel = QWidget()
        manual = QVBoxLayout(self.manual_panel)
        manual.setContentsMargins(0, 0, 0, 0)
        self.manual_text = QPlainTextEdit()
        self.manual_text.setAccessibleName("Roteiro manual")
        self.manual_text.setPlaceholderText("Cole ou digite uma linha de narração por cena.")
        manual.addWidget(self.manual_text, 1)
        self.manual_metrics = QLabel()
        self.manual_metrics.setWordWrap(True)
        manual.addWidget(self.manual_metrics)
        actions = QHBoxLayout()
        self.manual_import = self.button("Importar TXT", self.import_manual, actions)
        self.manual_clear = self.button("Limpar", self.manual_text.clear, actions)
        self.manual_save = self.button("Salvar roteiro", lambda: self.save_manual(), actions)
        self.manual_approve = self.button("Aprovar e preparar cenas", lambda: self.save_manual(approve=True),
                                          actions, True)
        manual.addLayout(actions)
        page.addWidget(self.manual_panel, 1)
        self.manual_text.textChanged.connect(self.update_manual_metrics)
        self.creation_mode.currentIndexChanged.connect(self.update_creation_mode)
        self.current = QLabel()
        self.current.setWordWrap(True)
        self.current.setTextFormat(Qt.TextFormat.PlainText)
        page.addWidget(self.current)
        self.project_actions = QWidget()
        buttons = QHBoxLayout(self.project_actions)
        buttons.setContentsMargins(0, 0, 0, 0)
        self.resume = self.button("Preparar cenas do projeto atual", self.resume_project, buttons)
        self.stop = self.button("Interromper e salvar", self.stop_task, buttons)
        self.export_button = self.button("Exportar MP4, SRT, ASS e créditos", self.export_project, buttons)
        self.new_manual = self.button("Novo roteiro", self.new_manual_project, buttons)
        page.addWidget(self.project_actions)
        self.dashboard_spacer = QWidget()
        page.addWidget(self.dashboard_spacer, 1)
        self.update_creation_mode()

    def update_creation_mode(self):
        manual = self.creation_mode.currentIndex() == 1
        self.manual_panel.setVisible(manual)
        self.dashboard_spacer.setVisible(not manual)
        for widget in (self.automatic, self.script_only, self.auto_approved, self.category, self.category_label):
            widget.setVisible(not manual)
        if hasattr(self, "open_button"):
            self.update_controls()
        else:
            self.update_manual_metrics()

    def update_manual_metrics(self):
        text = self.manual_text.toPlainText()
        lines = manual_lines(text)
        counts = [len(words(line)) for line in lines]
        issues = scene_issues(counts)
        invalid = invalid_manual_lines(counts)
        lo, hi = STRICT_RANGE
        self.manual_metrics.setText(
            f"{len(lines)}/{SCENE_COUNT} linhas · {sum(counts)} palavras ({lo}–{hi}) · "
            f"gancho {counts[0] if counts else 0}/{MAX_HOOK}"
            + ("\n" + issues[0] if issues else "")
            + (f" (+{len(issues) - 1})" if len(issues) > 1 else "")
        )
        selections = []
        for index in invalid:
            selection = QTextEdit.ExtraSelection()
            selection.cursor = QTextCursor(self.manual_text.document().findBlockByNumber(index))
            selection.format.setBackground(QColor("#682e39"))
            selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            selections.append(selection)
        self.manual_text.setExtraSelections(selections)
        idle = self.thread is None
        self.manual_approve.setEnabled(idle and not issues)
        existing = bool(self.project and "manual_script" in self.project)
        self.manual_save.setEnabled(idle and (bool(text) or existing))
        self.manual_clear.setEnabled(idle and bool(text))

    def import_manual(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Importar roteiro", filter="Texto UTF-8 (*.txt)")
        if filename:
            try:
                self.manual_text.setPlainText(Path(filename).read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError) as error:
                QMessageBox.warning(self, "Importar roteiro", f"Não foi possível ler o TXT em UTF-8. {error}")

    def save_manual(self, approve=False):
        if self.thread or not self.save_script():
            return
        text = self.manual_text.toPlainText()
        existing = self.project and "manual_script" in self.project
        topic = self.topic.text().strip() or "Roteiro manual"
        project = copy.deepcopy(self.project) if existing else {
            "scenes": [], "title": topic, "description": "", "hashtags": "",
        }
        try:
            if project.get("manual_script") != text:
                project.update(manual_script=text, manual_approved=False, status="Rascunho manual")
            if approve:
                approve_manual(project, text)
            if existing:
                self.store.save(project)
            else:
                project = self.store.new(topic, self.category.currentText(), **project)
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Roteiro manual", str(error))
            return
        self.project = project
        self.refresh()
        if approve:
            self.start_project("Vídeo")
        else:
            self.status.setText("Roteiro manual salvo no histórico.")

    def new_manual_project(self):
        if self.manual_text.toPlainText() != self.project["manual_script"]:
            answer = QMessageBox.question(self, "Novo roteiro", "Descartar as alterações ainda não salvas?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.project = None
        self.manual_text.clear()
        self.topic.clear()
        self.refresh()

    def editor(self):
        page = self.page("Editor de roteiro")
        self.editor_hint = QLabel("Uma linha por cena. As fontes e os trechos registrados aparecem abaixo.",
                                  objectName="muted")
        self.editor_hint.setWordWrap(True)
        page.addWidget(self.editor_hint)
        self.script_text = QPlainTextEdit()
        self.script_text.setAccessibleName("Narração das cenas")
        page.addWidget(self.script_text, 2)
        self.script_metrics = QLabel(objectName="muted")
        self.script_metrics.setWordWrap(True)
        page.addWidget(self.script_metrics)
        self.script_text.textChanged.connect(self.update_script_metrics)
        self.review_hint = QLabel(objectName="muted")
        self.review_hint.setWordWrap(True)
        page.addWidget(self.review_hint)
        self.save_script_button = self.button("Salvar roteiro editado", self.save_script, page)
        self.approve_script_button = self.button("Aprovar roteiro revisado", self.approve_script, page, True)
        self.sources = QPlainTextEdit(readOnly=True)
        self.sources.setAccessibleName("Fontes e evidências")
        page.addWidget(self.sources, 1)

    def gallery(self):
        page = self.page("Revisão de cenas")
        self.gallery_status = QLabel(objectName="muted")
        page.addWidget(self.gallery_status)
        self.graphic_preview = QLabel()
        self.graphic_preview.setAccessibleName("Prévia do PNG da cena")
        self.graphic_preview.hide()
        self.images = QListWidget()
        self.images.setIconSize(QSize(72, 100))
        self.images.setAccessibleName("Imagens e atribuições por cena")
        self.images.currentRowChanged.connect(self.show_scene)
        self.images.itemDoubleClicked.connect(self.preview_image)
        page.addWidget(self.images, 2)
        self.scene_details = QPlainTextEdit(readOnly=True)
        self.scene_details.setAccessibleName("Narração, consultas e prévia de legendas")
        details = QHBoxLayout()
        details.addWidget(self.scene_details, 1)
        details.addWidget(self.graphic_preview)
        page.addLayout(details, 1)
        actions = QHBoxLayout()
        self.retry = self.button("Buscar outra imagem", self.find_image, actions)
        self.replace = self.button("Selecionar imagem local", self.replace_image, actions)
        self.replace_secondary = self.button("Definir imagem adicional",
                                             lambda: self.replace_image(secondary=True), actions)
        page.addLayout(actions)
        self.prepare_button = self.button("Buscar imagens pendentes e preparar cenas",
                                          lambda: self.start_project("Preparar"), page)
        self.preview_motion = self.button("Prévia de 5 s · 540 × 960", lambda: self.start_project("Prévia"), page)
        self.render_button = self.button("Renderizar projeto",
                                         lambda: self.start_project("Renderizar"), page, True)

    def show_scene(self, row):
        if row < 0 or not self.project:
            self.scene_details.clear()
            self.graphic_preview.hide()
        else:
            scene = self.project["scenes"][row]
            image = scene.get("image", {})
            queries = " · ".join(scene.get("keywords") or [scene.get("query", "")])
            graphic = scene.get("graphic", {})
            if generic_graphic(graphic):
                graphic = {}
            pixmap = QPixmap(str(self.store.folder(self.project) / graphic["file"])) if graphic else QPixmap()
            if not pixmap.isNull():
                self.graphic_preview.setPixmap(pixmap.scaled(112, 128, Qt.AspectRatioMode.KeepAspectRatio,
                                                           Qt.TransformationMode.SmoothTransformation))
            self.graphic_preview.setVisible(not pixmap.isNull())
            effect = scene.get("edit", {})
            labels = {"camera": "Câmera", "entrance": "Entrada", "exit": "Saída",
                      "transition": "Transição", "sound": "Som"}
            effects = " · ".join(f"{label} {effect[key]}" for key, label in labels.items() if key in effect)
            secondary = scene.get("secondary_image") or scene.get("video") or {}
            captions = "\n\n".join("\n".join(lines) for lines in blocks(scene["text"]))
            self.scene_details.setPlainText(
                f'Narração: {scene["text"]}\nConsultas: {queries}\n'
                f'Provedor: {image.get("provider", "Pendente")}\n'
                f'Visual adicional: {secondary.get("title", "—")}\n'
                f'PNG: {graphic.get("title", "—")}\nEfeitos: {effects or "Pendentes"}\n'
                f'Consulta utilizada: {image.get("search_query", "—")}\n\n'
                f"Prévia dos blocos de legenda\n{captions}\n\n"
                + ("Destaques: tempos de palavras do sintetizador." if scene.get("word_timings")
                   else "Destaques: posições estimadas dentro dos blocos de fala.")
            )
        self.update_controls()

    def preview_image(self):
        row = self.images.currentRow()
        if row < 0:
            return
        image = self.project["scenes"][row].get("image")
        if not image or placeholder(image):
            return
        path = self.store.folder(self.project) / image["file"]
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Imagem da cena {row + 1}")
        layout = QVBoxLayout(dialog)
        label = QLabel()
        label.setPixmap(pixmap.scaled(560, 580, Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation))
        layout.addWidget(label)
        dialog.exec()

    def history_page(self):
        page = self.page("Histórico")
        self.history = QListWidget()
        page.addWidget(self.history, 1)
        self.history.currentRowChanged.connect(lambda index: self.update_controls())
        self.history.itemDoubleClicked.connect(lambda item: self.open_project())
        self.open_button = self.button("Abrir projeto selecionado", self.open_project, page)

    def settings_page(self):
        page = self.page("Configurações")
        self.config_fields = {}
        self.initial = defaults()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        fields_widget = QWidget()
        form = QFormLayout(fields_widget)
        scroll.setWidget(fields_widget)
        fields = {
            "model": "Modelo Ollama", "ollama": "URL do Ollama",
            "light": "Modo leve · IA em CPU, sem ComfyUI",
            "flexible_script": "Roteiro flexível · 130 a 190 palavras",
            "piper_model": "Modelo Piper pt-BR (.onnx)", "piper_bin": "Executável Piper",
            "comfy_enabled": "Usar ComfyUI", "comfy": "URL do ComfyUI",
            "workflow": "Workflow ComfyUI (JSON API)",
            "pexels": "Chave gratuita Pexels", "pixabay": "Chave gratuita Pixabay",
            "media_library": "Pasta de imagens, PNGs e vídeos locais", "music": "Arquivo opcional de música",
            "online_graphics": "Buscar PNGs em serviços gratuitos",
        }
        for key, label in fields.items():
            value = self.initial[key]
            if isinstance(value, bool):
                field = QCheckBox()
                field.setChecked(self.settings.value(key, value, type=bool))
            else:
                field = QLineEdit(self.settings.value(key, value, type=str))
                if key in ("pexels", "pixabay"):
                    field.setEchoMode(QLineEdit.EchoMode.Password)
            self.config_fields[key] = field
            form.addRow(label, field)
        self.voice.blockSignals(True)
        index = self.voice.findData(self.settings.value("voice", self.initial["voice"]))
        self.voice.setCurrentIndex(max(0, index))
        self.voice.blockSignals(False)
        page.addWidget(scroll, 1)
        self.save_settings_button = self.button("Salvar configurações e verificar dependências",
                                                self.save_settings, page)
        page.addStretch()

    def get_config(self):
        return {**{key: field.isChecked() if isinstance(field, QCheckBox) else field.text().strip()
                   for key, field in self.config_fields.items()}, "voice": self.voice.currentData(),
                "edit_mode": self.edit_mode.currentText(), "intensity": self.intensity.value()}

    def save_settings(self):
        for key, value in self.get_config().items():
            self.settings.setValue(key, value)
        self.settings.sync()
        self.update_script_metrics()
        self.check_dependencies()

    def check_dependencies(self):
        config = self.get_config()
        def verify(progress, check):
            ffmpeg = "FFmpeg disponível" if binary("ffmpeg") and binary("ffprobe") else "FFmpeg/FFprobe ausentes"
            command = "CLI instalada" if binary("ollama") else "CLI ausente no PATH"
            try:
                with session() as client:
                    response = client.get(config["ollama"].rstrip("/") + "/api/tags", timeout=3)
                    response.raise_for_status()
                    names = {model["name"] for model in response.json()["models"]}
                model = config["model"]
                available = model in names or model + ":latest" in names
                service = "Ollama ativo" if available else f"Ollama ativo, baixe {model}"
            except Exception:
                service = "Ollama inacessível"
            return f"{ffmpeg}\n{service}\n{command}"
        self.execute(verify)

    def refresh(self):
        self.history.clear()
        for project in self.store.history():
            title = project.get("title") or project["topic"] or project["category"]
            item = QListWidgetItem(f'{title}  ·  {project["status"]}')
            item.setData(Qt.ItemDataRole.UserRole, project["id"])
            self.history.addItem(item)
        selected_row = self.images.currentRow()
        self.images.clear()
        self.script_text.clear()
        self.sources.clear()
        project = self.project
        if not project:
            self.current.setText("Nenhum projeto aberto.")
        else:
            title = project.get("title") or project["topic"] or project["category"]
            self.current.setText(f'{title} · {project["status"]}')
            self.script_text.setPlainText("\n".join(scene["text"] for scene in project["scenes"]))
            source_names = {item["id"]: item["title"] for item in project["sources"]}
            sources = [f'{item["title"]} — {item["url"]}' for item in project["sources"]]
            for index, scene in enumerate(project["scenes"], 1):
                citation = source_names.get(scene["source_id"], "Sem evidência validada")
                edited = " · texto editado manualmente" if scene.get("edited") else ""
                sources.append(f'Cena {index} · {citation}{edited}\n{scene["evidence"]}')
                image = scene.get("image", {})
                if placeholder(image):
                    image = {}
                status = scene.get("image_status", "Imagem válida" if image else "Imagem pendente")
                name = image.get("title", "Imagem pendente")
                item = QListWidgetItem(f'{index:02d}  {name}\n' + " · ".join(filter(None, (
                    status, image.get("provider"), image.get("license")))))
                item.setToolTip("\n".join(filter(None, (scene["prompt"], image.get("url"),
                                                        image.get("license_url"), *image.get("notes", [])))))
                if image:
                    pixmap = QPixmap(str(self.store.folder(project) / image["file"]))
                    if not pixmap.isNull():
                        item.setIcon(QIcon(pixmap.scaled(72, 100, Qt.AspectRatioMode.KeepAspectRatio,
                                                         Qt.TransformationMode.SmoothTransformation)))
                self.images.addItem(item)
            metadata = "\n".join(project.get(field, "") for field in ("title", "description", "hashtags"))
            self.sources.setPlainText(metadata.strip() + "\n\n" + "\n\n".join(sources))
        manual = bool(project and "manual_script" in project)
        self.editor_hint.setText(
            "Edite e aprove o roteiro manual pelo Dashboard." if manual
            else "Uma linha por cena. As fontes e os trechos registrados aparecem abaixo."
        )
        review = project.get("script_review") if project else None
        self.review_hint.setText("Revise as cenas e confira as fontes antes de aprovar." if review else "")
        self.review_hint.setVisible(bool(review))
        if review:
            self.sources.appendPlainText("\nValidação automática\n" + "\n".join(review))
        self.script_text.document().setModified(False)
        self.update_script_metrics()
        self.update_controls()
        if self.images.count():
            self.images.setCurrentRow(max(0, min(selected_row, self.images.count() - 1)))

    def editor_lines(self):
        lines = [line.strip() for line in self.script_text.toPlainText().split("\n")]
        if len(lines) == SCENE_COUNT + 1 and not lines[-1]:
            lines.pop()
        return lines

    def update_script_metrics(self):
        counts = scene_counts([{"text": line} for line in self.editor_lines()])
        total = sum(counts)
        limits = word_range(self.get_config()) if hasattr(self, "config_fields") else word_range({})
        self.script_metrics.setText(
            f"{total} palavras · faixa {limits[0]}–{limits[1]} · "
            f"gancho {counts[0]}/{MAX_HOOK} · estimativa de fala {estimated_seconds(total)} s · vídeo 70 s"
        )

    def update_controls(self):
        idle = self.thread is None
        has_project = bool(self.project)
        has_scenes = has_project and bool(self.project["scenes"])
        manual = has_project and "manual_script" in self.project
        pending_manual = manual and not self.project.get("manual_approved")
        self.manual_text.setReadOnly(not idle)
        self.update_manual_metrics()
        for widget in (self.creation_mode, self.manual_import, self.automatic, self.script_only,
                       self.save_settings_button, self.category,
                       self.topic, self.voice, self.edit_mode, self.intensity, self.auto_approved, *self.config_fields.values()):
            widget.setEnabled(idle)
        review = has_project and bool(self.project.get("script_review"))
        self.resume.setEnabled(idle and has_project and not review and not pending_manual)
        self.approve_script_button.setVisible(review)
        self.approve_script_button.setEnabled(idle and review)
        self.stop.setEnabled(not idle and bool(self.work_id))
        folder = self.store.folder(self.project) if has_scenes else None
        valid = {index for index, scene in enumerate(self.project["scenes"]) if
                 (image := scene.get("image")) and image.get("file") and not placeholder(image)
                 and (folder / image["file"]).is_file()
                 and scene.get("image_status") == "Imagem válida"} if folder else set()
        self.gallery_status.setText(f"{len(valid)}/{SCENE_COUNT} cenas com imagens válidas")
        ready = bool(has_scenes and self.project.get("media_review") and len(valid) == SCENE_COUNT)
        self.export_button.setEnabled(idle and ready and self.project["status"] == "Pronto")
        manual_mode = self.creation_mode.currentIndex() == 1
        self.resume.setVisible(not manual_mode or (self.resume.isEnabled() and not self.export_button.isEnabled()))
        self.stop.setVisible(not manual_mode or self.stop.isEnabled())
        self.export_button.setVisible(not manual_mode or self.export_button.isEnabled())
        self.new_manual.setVisible(manual_mode and manual and idle)
        self.project_actions.setVisible(any(not button.isHidden() for button in (
            self.resume, self.stop, self.export_button, self.new_manual,
        )))
        self.save_script_button.setEnabled(idle and has_scenes and not manual)
        self.script_text.setReadOnly(not (idle and has_scenes and not manual))
        selected = idle and has_scenes and not pending_manual and self.images.currentRow() >= 0
        self.replace.setEnabled(selected)
        self.replace_secondary.setEnabled(selected and self.images.currentRow() in valid)
        self.retry.setEnabled(selected and not review)
        self.render_button.setEnabled(idle and ready and not review and not pending_manual)
        self.prepare_button.setEnabled(idle and has_scenes and not review and not pending_manual)
        self.preview_motion.setEnabled(idle and self.images.currentRow() in valid and not review and not pending_manual)
        self.open_button.setEnabled(idle and self.history.currentRow() >= 0)

    def save_script(self):
        if not self.project or "manual_script" in self.project or not self.script_text.document().isModified():
            return True
        try:
            lines = self.editor_lines()
            project = copy.deepcopy(self.project)
            if project.get("script_review"):
                if len(lines) != SCENE_COUNT:
                    raise ValueError("Mantenha 14 linhas, uma para cada cena.")
            else:
                validate_scenes([{"text": text} for text in lines], manual=True)
            for scene, text in zip(project["scenes"], lines, strict=True):
                if scene["text"] != text:
                    scene["text"] = text
                    scene["edited"] = True
                    scene.pop("keywords", None)
                    scene.pop("secondary_image", None)
                    scene.pop("graphic", None)
                    scene.pop("video", None)
                    image = scene.get("image", {})
                    if image and image.get("provider") != "Imagem manual":
                        scene.pop("image", None)
                        scene["image_status"] = "Imagem pendente"
                    project["media_review"] = False
            project["status"] = "Revisão pendente" if project.get("script_review") else "Roteiro editado"
            self.store.save(project)
            self.project = project
            self.refresh()
            return True
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Roteiro", str(error))
            return False

    def approve_script(self):
        if not self.save_script():
            return
        try:
            project = copy.deepcopy(self.project)
            validate_scenes(project["scenes"], manual=True)
            project.pop("script_review")
            project["status"] = "Roteiro aprovado"
            self.store.save(project)
            self.project = project
            self.refresh()
            if self.auto_approved.isChecked():
                self.start_project("Vídeo")
            else:
                self.status.setText("Roteiro aprovado. Retome o projeto pelo Dashboard.")
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Roteiro", str(error))

    def start_new(self, mode):
        if not self.save_script():
            return
        self.project = self.store.new(self.topic.text().strip(), self.category.currentText())
        self.manual_text.clear()
        self.refresh()
        self.start_project(mode)

    def resume_project(self):
        if self.save_script():
            self.start_project("Vídeo")

    def open_project(self):
        if self.thread or not self.history.currentItem():
            return
        selected = self.history.currentItem().data(Qt.ItemDataRole.UserRole)
        if self.save_script():
            self.project = next(item for item in self.store.history() if item["id"] == selected)
            self.refresh()
            if "manual_script" in self.project:
                self.topic.setText("" if self.project["topic"] == "Roteiro manual" else self.project["topic"])
                self.category.setCurrentText(self.project["category"])
                self.manual_text.setPlainText(self.project["manual_script"])
                self.creation_mode.setCurrentIndex(1)
                self.nav.setCurrentRow(0)
            else:
                self.manual_text.clear()
                self.creation_mode.setCurrentIndex(0)
                self.nav.setCurrentRow(1 if self.project["scenes"] else 0)

    def find_image(self):
        row = self.images.currentRow()
        if not self.save_script():
            return
        project = copy.deepcopy(self.project)
        config = self.get_config()
        def operation(progress, check):
            progress(20, f"Buscando alternativa para a cena {row + 1}")
            return retry_image(self.store, project, config, row, progress, check)
        self.execute(operation, project["id"], 2)

    def replace_image(self, *, secondary=False):
        row = self.images.currentRow()
        if not self.save_script():
            return
        title = "Selecionar imagem adicional" if secondary else "Selecionar imagem principal"
        filename, _ = QFileDialog.getOpenFileName(
            self, title, filter="Imagens (*.jpg *.jpeg *.png *.webp *.tif *.tiff)")
        if not filename:
            return
        project = copy.deepcopy(self.project)
        folder = self.store.folder(project)
        target = f"manual-{uuid.uuid4().hex}.jpg"
        credit = {"provider": "Imagem manual", "file": target, "title": Path(filename).name}
        def import_image(progress, check):
            if placeholder(credit) or marked(credit):
                raise ValueError("Selecione uma imagem temática sem marca-d’água.")
            normalize(filename, folder / target)
            check()
            credit.update(fingerprints(folder / target))
            key = "secondary_image" if secondary else "image"
            existing = [
                image for index, scene in enumerate(project["scenes"])
                for image_key in (("image", "secondary_image") if secondary else ("image",))
                if (index, image_key) != (row, key) and (image := existing_image(scene, folder, image_key))
            ]
            if duplicate(credit, existing):
                (folder / target).unlink()
                raise ValueError("Esta imagem é igual ou muito semelhante à de outra cena.")
            scene = project["scenes"][row]
            scene[key] = credit
            if secondary:
                scene.pop("video", None)
            scene["image_status"] = "Imagem válida"
            validate_secondary(project, folder)
            missing = missing_images(project, folder)
            project["media_review"] = not missing and all(scene.get("edit") for scene in project["scenes"])
            project["status"] = ("Revisão pendente" if project.get("script_review")
                                 else "Imagens pendentes" if missing else "Revisar cenas")
            logging.getLogger(__name__).info("Imagem manual | cena=%02d | %s | %s", row + 1, filename, folder / target)
            self.store.save(project)
            return "Imagem substituída."
        self.execute(import_image, project["id"])

    def start_project(self, mode):
        if mode in ("Renderizar", "Preparar", "Prévia") and not self.save_script():
            return
        project = copy.deepcopy(self.project)
        config = self.get_config()
        if mode == "Prévia":
            config["preview_index"] = self.images.currentRow()
        def operation(progress, check):
            process(self.store, project, config, mode, progress, check)
        self.execute(operation, project["id"], {"Roteiro": 1, "Vídeo": 2, "Preparar": 2, "Prévia": 2, "Renderizar": 2}.get(mode),
                     preview_job=mode == "Prévia")

    def execute(self, operation, work_id=None, desired_page=None, export_job=False, preview_job=False):
        self.work_id = work_id
        self.desired_page = desired_page
        self.export_job = export_job
        self.preview_job = preview_job
        self.result = None
        self.progress.setValue(0)
        self.thread = QThread(self)
        self.task = Task(operation)
        self.task.moveToThread(self.thread)
        self.thread.started.connect(self.task.run)
        self.task.progress.connect(self.on_progress)
        self.task.done.connect(self.on_done)
        self.task.done.connect(self.thread.quit)
        self.thread.finished.connect(self.task.deleteLater)
        self.thread.finished.connect(self.finished)
        self.update_controls()
        self.thread.start()

    @Slot(int, str)
    def on_progress(self, value, label):
        self.progress.setValue(value)
        self.status.setText(label)

    @Slot(str, str)
    def on_done(self, outcome, message):
        self.result = outcome, message

    @Slot()
    def finished(self):
        thread = self.thread
        self.thread = self.task = None
        thread.deleteLater()
        outcome, message = self.result or ("error", "A tarefa terminou sem resultado.")
        if self.work_id:
            self.project = next(item for item in self.store.history() if item["id"] == self.work_id)
            self.refresh()
            if outcome == "ok" and self.project.get("script_review"):
                self.nav.setCurrentRow(1)
            elif outcome == "ok" and self.desired_page is not None:
                self.nav.setCurrentRow(self.desired_page)
                if self.preview_job:
                    path = self.store.folder(self.project) / "previa" / "video.mp4"
                    if path.is_file():
                        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        elif outcome == "ok" and not self.export_job:
            self.dependencies.setText(message)
            self.status.setText("Pronto")
        if self.work_id or self.export_job:
            if message:
                self.status.setText(message if outcome != "error" else "Falha durante o processamento")
            if outcome == "error":
                QMessageBox.warning(self, APP, message)
        self.work_id = None
        self.update_controls()
        if self.close_pending:
            self.close()

    def stop_task(self):
        if self.task:
            self.task.stop.set()
            self.status.setText("Interrompendo ao concluir a operação atual…")

    def export_project(self):
        if not self.save_script() or self.project["status"] != "Pronto" or not self.project.get("media_review"):
            return
        filename, _ = QFileDialog.getSaveFileName(self, "Salvar vídeo", "curiosidade.mp4", "Vídeo (*.mp4)")
        if not filename:
            return
        target = Path(filename).with_suffix(".mp4")
        folder = self.store.folder(self.project)
        self.execute(lambda progress, check: (export(folder, target), "Vídeo, legendas e créditos exportados.")[1],
                     export_job=True)

    def closeEvent(self, event):
        if self.thread:
            self.close_pending = True
            if self.work_id:
                self.stop_task()
            event.ignore()
        elif self.save_script():
            event.accept()
        else:
            event.ignore()

def main():
    app = QApplication([])
    app.setOrganizationName("CuriosityStudio")
    app.setApplicationName(APP)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = Window()
    handler = RotatingFileHandler(window.store.root / "validacao.log", maxBytes=512_000,
                                  backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger = logging.getLogger("studio")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        window.show()
        return app.exec()
    finally:
        logger.removeHandler(handler)
        handler.close()
