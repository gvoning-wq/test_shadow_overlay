# ui/overlay_window.py
from PyQt6.QtWidgets import (
    QWidget, QPushButton, QHBoxLayout, QVBoxLayout,
    QLabel, QComboBox, QMenu, QApplication
)
from PyQt6.QtCore import Qt, QRect, QPoint, pyqtSignal, QTimer, QEvent, QObject
from PyQt6.QtGui import QIcon, QPixmap, QImage, QPainter, QPen, QBrush, QColor
from PyQt6.QtGui import QKeySequence, QShortcut, QCursor, QScreen
import numpy as np
from scipy.ndimage import gaussian_filter
import logging
import sys

class DraggableRect:
    def __init__(self, x, y, w, h, color=None, mode="solid", effect_type="blur", effect_param=None):
        self.rect = QRect(x, y, w, h)
        self.dragging = False
        self.resizing = False
        self.resize_handle = None
        self.corner_size = 15
        self.offset = QPoint(0, 0)
        self.start_rect = QRect()
        self.color = color if color else QColor(0, 0, 0, 180)
        self.mode = mode  # "solid" или "frosted"
        self.effect_type = effect_type  # "blur", "mosaic", "grayscale"
        self.effect_param = effect_param  # sigma, block_size и т.д.

    def contains(self, pos):
        return self.rect.contains(pos)

    def get_resize_handle_at(self, pos):
        """Определяет, где находится позиция: в углу, на стороне или внутри"""
        # pos - это координаты в системе координат прямоугольника (0,0 в левом верхнем углу прямоугольника)
        x, y = pos.x(), pos.y()
        margin = self.corner_size
        
        # Используем границы прямоугольника в его локальной системе координат
        left = 0
        right = self.rect.width()
        top = 0
        bottom = self.rect.height()

        # Углы (приоритетнее, чем стороны)
        if x < margin and y < margin:
            return 'nw'
        if x > right - margin and y < margin:
            return 'ne'
        if x > right - margin and y > bottom - margin:
            return 'se'
        if x < margin and y > bottom - margin:
            return 'sw'

        # Стороны
        if x < margin and margin <= y <= bottom - margin:
            return 'w'
        if x > right - margin and margin <= y <= bottom - margin:
            return 'e'
        if y < margin and margin <= x <= right - margin:
            return 'n'
        if y > bottom - margin and margin <= x <= right - margin:
            return 's'

        return None

    def get_cursor_for_handle(self, handle):
        """Возвращает курсор для конкретного хэндла"""
        cursors = {
            'nw': Qt.CursorShape.SizeFDiagCursor,
            'se': Qt.CursorShape.SizeFDiagCursor,
            'ne': Qt.CursorShape.SizeBDiagCursor,
            'sw': Qt.CursorShape.SizeBDiagCursor,
            'n': Qt.CursorShape.SizeVerCursor,
            's': Qt.CursorShape.SizeVerCursor,
            'w': Qt.CursorShape.SizeHorCursor,
            'e': Qt.CursorShape.SizeHorCursor,
        }
        return cursors.get(handle, Qt.CursorShape.ArrowCursor)


class SelectionOverlay(QWidget):
    area_selected = pyqtSignal(int, int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowState(Qt.WindowState.WindowFullScreen)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self.escape_sc = QShortcut(QKeySequence("Esc"), self)
        self.escape_sc.activated.connect(self.close)

        self.start_pos = None
        self.current_rect = QRect()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_pos = event.position().toPoint()
            self.current_rect = QRect(self.start_pos, self.start_pos)
            self.update()

    def mouseMoveEvent(self, event):
        if self.start_pos:
            pos = event.position().toPoint()
            self.current_rect = QRect(self.start_pos, pos).normalized()
            self.update()

    def mouseReleaseEvent(self, event):
        if self.current_rect.width() > 10 and self.current_rect.height() > 10:
            rect = self.current_rect.normalized()
            self.area_selected.emit(rect.x(), rect.y(), rect.width(), rect.height())
        self.close()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        shade_color = QColor(0, 100, 0, 50)
        painter.fillRect(self.rect(), shade_color)

        if self.start_pos is not None and not self.current_rect.isNull():
            rect = self.current_rect.normalized()
            pen = QPen(QColor(255, 255, 255))
            pen.setWidth(2)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)

class CanvasOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        
        # ВАЖНО: отключаем прием событий от мыши, окно будет их пропускать
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        
        self.setWindowState(Qt.WindowState.WindowFullScreen)
        self.setAttribute(Qt.WidgetAttribute.WA_MouseTracking, True)
        self.setMouseTracking(True)

        self.rectangles = []
        self.current_hover_rect = None
        self.current_hover_handle = None
        self.is_dragging_or_resizing = False

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.context_menu)

        # Таймер обновления курсора
        self.cursor_timer = QTimer()
        self.cursor_timer.timeout.connect(self.update_cursor_and_hit_test)
        self.cursor_timer.start(20)

        self.current_color = QColor(0, 0, 0, 255)  # Цвет по умолчанию

        # --- НОВОЕ: таймер для ограничения обновления ---
        self.update_timer = QTimer()
        self.update_timer.setSingleShot(True)
        self.update_timer.timeout.connect(self.update)  # Вызов update() через таймер

        self.last_update_time = 0
        self.update_interval = 0.1  # Интервал в секундах (150 мс)

    def context_menu(self, pos):
        # Если окно прозрачно для событий, не показываем меню
        if self.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents):
            return
            
        rect = self.get_rect_at(pos)
        if rect:
            menu = QMenu()
            action = menu.addAction("Удалить")
            action.triggered.connect(lambda: self.delete_rect(rect))
            menu.exec(self.mapToGlobal(pos))

    def delete_rect(self, rect):
        self.rectangles.remove(rect)
        self.update()

    def get_rect_at(self, pos):
        for r in reversed(self.rectangles):
            if r.rect.contains(pos):
                return r
        return None

    def add_rectangle(self, x, y, w, h, color=None, mode="solid", effect_type="blur", effect_param=None):
        """Добавляет прямоугольник с указанным визуальным эффектом"""
        if color is None:
            color = QColor(0, 0, 0, 180)
        
        rect = DraggableRect(
            x, y, w, h,
            color=color,
            mode=mode,
            effect_type=effect_type,
            effect_param=effect_param
        )
        self.rectangles.append(rect)
        self.update()

    def update_cursor_and_hit_test(self):
        """Обновляет курсор и проверяет, нужно ли перехватывать события мыши"""
        pos = QCursor.pos()
        local_pos = self.mapFromGlobal(pos)
        
        found_rect = None
        found_handle = None
        
        # Проверяем все прямоугольники
        for rect in reversed(self.rectangles):
            if rect.rect.contains(local_pos):
                found_rect = rect
                rect_local_pos = local_pos - rect.rect.topLeft()
                handle = rect.get_resize_handle_at(rect_local_pos)
                if handle:
                    found_handle = handle
                break
        
        # Если нашли прямоугольник под курсором - включаем прием событий
        if found_rect:
            if self.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents):
                self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        else:
            # Нет прямоугольника - выключаем прием событий
            if not self.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents):
                self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        
        # Обновляем курсор
        if self.is_dragging_or_resizing:
            return
            
        if found_handle:
            cursor_shape = found_rect.get_cursor_for_handle(found_handle)
            self.setCursor(cursor_shape if cursor_shape else Qt.CursorShape.ArrowCursor)
        elif found_rect:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            
        self.current_hover_rect = found_rect
        self.current_hover_handle = found_handle

    def mousePressEvent(self, event):
        # Если окно прозрачно для событий, не обрабатываем
        if self.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents):
            return
            
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            
            for rect in reversed(self.rectangles):
                if rect.rect.contains(pos):
                    rect_local_pos = pos - rect.rect.topLeft()
                    handle = rect.get_resize_handle_at(rect_local_pos)
                    
                    if handle:
                        rect.resizing = True
                        rect.resize_handle = handle
                        rect.start_pos = pos
                        rect.start_rect = QRect(rect.rect)
                        cursor_shape = rect.get_cursor_for_handle(handle)
                        if cursor_shape:
                            self.setCursor(cursor_shape)
                    else:
                        rect.dragging = True
                        rect.offset = pos - rect.rect.topLeft()
                        self.setCursor(Qt.CursorShape.ClosedHandCursor)
                    
                    self.is_dragging_or_resizing = True
                    self.update()
                    return

    def mouseMoveEvent(self, event):
        # Если окно прозрачно для событий, не обрабатываем
        if self.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents):
            return
            
        pos = event.position().toPoint()
        
        if not event.buttons() & Qt.MouseButton.LeftButton:
            self.update_cursor_and_hit_test()
            return
        
        for rect in self.rectangles:
            if rect.dragging:
                new_top_left = pos - rect.offset
                rect.rect.moveTopLeft(new_top_left)
                self.request_update()
                return
            
            if rect.resizing:
                r = QRect(rect.start_rect)
                handle = rect.resize_handle
                delta = pos - rect.start_pos
                
                if handle == 'nw':
                    r.setTopLeft(rect.start_rect.topLeft() + delta)
                elif handle == 'ne':
                    new_top = rect.start_rect.top() + delta.y()
                    new_right = rect.start_rect.right() + delta.x()
                    r.setTopRight(QPoint(new_right, new_top))
                elif handle == 'se':
                    r.setBottomRight(rect.start_rect.bottomRight() + delta)
                elif handle == 'sw':
                    new_bottom = rect.start_rect.bottom() + delta.y()
                    new_left = rect.start_rect.left() + delta.x()
                    r.setBottomLeft(QPoint(new_left, new_bottom))
                elif handle == 'n':
                    r.setTop(rect.start_rect.top() + delta.y())
                elif handle == 's':
                    r.setBottom(rect.start_rect.bottom() + delta.y())
                elif handle == 'w':
                    r.setLeft(rect.start_rect.left() + delta.x())
                elif handle == 'e':
                    r.setRight(rect.start_rect.right() + delta.x())
                
                if r.width() > 10 and r.height() > 10:
                    rect.rect = r.normalized()
                    self.update()
                return

    def mouseReleaseEvent(self, event):
        # Если окно прозрачно для событий, не обрабатываем
        if self.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents):
            return
            
        if event.button() == Qt.MouseButton.LeftButton:
            for rect in self.rectangles:
                rect.dragging = False
                rect.resizing = False
                rect.resize_handle = None
            
            self.is_dragging_or_resizing = False
            self.update_cursor_and_hit_test()
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 0))  # Полная прозрачность

        screen = QApplication.primaryScreen()

        for rect in self.rectangles:
            if rect.mode == "frosted":
                qr = rect.rect
                try:
                    screenshot = screen.grabWindow(0, qr.x(), qr.y(), qr.width(), qr.height())
                    image = screenshot.toImage()

                    # --- Конвертируем QImage в numpy array ---
                    if image.format() != QImage.Format.Format_RGBA8888:
                        image = image.convertToFormat(QImage.Format.Format_RGBA8888)

                    width = qr.width()
                    height = qr.height()
                    ptr = image.bits()
                    ptr.setsize(height * width * 4)
                    arr = np.array(ptr).reshape((height, width, 4)).astype(np.float32)

                    r = arr[:, :, 0]
                    g = arr[:, :, 1]
                    b = arr[:, :, 2]
                    a = arr[:, :, 3]

                    # --- Применяем эффект ---
                    if rect.effect_type == "blur":
                        sigma = rect.effect_param or 8
                        r_blur = gaussian_filter(r, sigma=sigma)
                        g_blur = gaussian_filter(g, sigma=sigma)
                        b_blur = gaussian_filter(b, sigma=sigma)
                        result = np.stack([r_blur, g_blur, b_blur, a], axis=2)

                    elif rect.effect_type == "mosaic":
                        block_size = max(1, rect.effect_param or 8)
                        h, w = arr.shape[:2]

                        # Обрезаем до кратного block_size
                        valid_h = (h // block_size) * block_size
                        valid_w = (w // block_size) * block_size
                        if valid_h == 0 or valid_w == 0:
                            result = arr.copy()
                        else:
                            cropped_r = r[:valid_h, :valid_w]
                            cropped_g = g[:valid_h, :valid_w]
                            cropped_b = b[:valid_h, :valid_w]

                            # Усредняем по блокам
                            blocks_r = cropped_r.reshape(valid_h // block_size, block_size, valid_w // block_size, block_size).mean(axis=(1, 3))
                            blocks_g = cropped_g.reshape(valid_h // block_size, block_size, valid_w // block_size, block_size).mean(axis=(1, 3))
                            blocks_b = cropped_b.reshape(valid_h // block_size, block_size, valid_w // block_size, block_size).mean(axis=(1, 3))

                            # Масштабируем обратно
                            mosaic_r = np.kron(blocks_r, np.ones((block_size, block_size)))
                            mosaic_g = np.kron(blocks_g, np.ones((block_size, block_size)))
                            mosaic_b = np.kron(blocks_b, np.ones((block_size, block_size)))

                            # Дополняем края
                            if mosaic_r.shape[0] < h:
                                mosaic_r = np.vstack([mosaic_r, np.tile(mosaic_r[-1:], (h - mosaic_r.shape[0], 1))])
                                mosaic_g = np.vstack([mosaic_g, np.tile(mosaic_g[-1:], (h - mosaic_g.shape[0], 1))])
                                mosaic_b = np.vstack([mosaic_b, np.tile(mosaic_b[-1:], (h - mosaic_b.shape[0], 1))])

                            if mosaic_r.shape[1] < w:
                                mosaic_r = np.hstack([mosaic_r, np.tile(mosaic_r[:, -1:], (1, w - mosaic_r.shape[1]))])
                                mosaic_g = np.hstack([mosaic_g, np.tile(mosaic_g[:, -1:], (1, w - mosaic_g.shape[1]))])
                                mosaic_b = np.hstack([mosaic_b, np.tile(mosaic_b[:, -1:], (1, w - mosaic_b.shape[1]))])

                            result = np.stack([mosaic_r, mosaic_g, mosaic_b, a], axis=2)

                    elif rect.effect_type == "grayscale":
                        gray = (0.299 * r + 0.587 * g + 0.114 * b)
                        result = np.stack([gray, gray, gray, a], axis=2)

                    else:
                        result = arr  # fallback

                    # --- Конвертируем обратно в QImage ---
                    result = np.clip(result, 0, 255).astype(np.uint8)
                    output_image = QImage(result.data, width, height, QImage.Format.Format_RGBA8888)
                    pixmap = QPixmap.fromImage(output_image)
                    painter.drawPixmap(qr, pixmap)

                except Exception as e:
                    logging.error(f"[CanvasOverlay] Ошибка эффекта: {e}")
                    painter.fillRect(qr, QColor(0, 0, 0, 100))

            else:
                painter.setBrush(QBrush(rect.color))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRect(rect.rect)

    def request_update(self):
        """Запрашивает обновление с ограничением по времени"""
        import time
        current_time = time.time()
        
        # Если прошло достаточно времени — обновляем сразу
        if current_time - self.last_update_time > self.update_interval:
            self.last_update_time = current_time
            self.update()
        else:
            # Иначе — планируем обновление через таймер
            if not self.update_timer.isActive():
                delay_ms = int((self.update_interval - (current_time - self.last_update_time)) * 1000)
                self.update_timer.start(max(1, delay_ms))

    def apply_effect(self, image: QImage) -> QImage:
        """Применяет выбранный визуальный эффект: blur, mosaic, grayscale"""
        width = image.width()
        height = image.height()

        if image.format() != QImage.Format.Format_RGBA8888:
            image = image.convertToFormat(QImage.Format.Format_RGBA8888)

        ptr = image.bits()
        ptr.setsize(height * width * 4)
        arr = np.array(ptr).reshape((height, width, 4))

        r = arr[:, :, 0].astype(np.float32)
        g = arr[:, :, 1].astype(np.float32)
        b = arr[:, :, 2].astype(np.float32)
        a = arr[:, :, 3]

        # Получаем данные эффекта из контекста
        # Мы передадим их позже
        pass  # см. ниже

    def draw_debug_handles(self, painter, rect):
        """Рисует отладочные маркеры для визуализации зон захвата"""
        r = rect.rect
        size = rect.corner_size
        
        # Угловые маркеры
        painter.setBrush(QBrush(QColor(255, 0, 0, 100)))
        painter.setPen(QPen(QColor(255, 0, 0), 1))
        
        corners = [
            QRect(r.left(), r.top(), size, size),  # NW
            QRect(r.right() - size, r.top(), size, size),  # NE
            QRect(r.right() - size, r.bottom() - size, size, size),  # SE
            QRect(r.left(), r.bottom() - size, size, size),  # SW
        ]
        
        for corner in corners:
            painter.drawRect(corner)
        
        # Боковые маркеры
        painter.setBrush(QBrush(QColor(0, 0, 255, 100)))
        painter.setPen(QPen(QColor(0, 0, 255), 1))
        
        mid_x = r.left() + r.width() // 2 - size // 2
        mid_y = r.top() + r.height() // 2 - size // 2
        
        sides = [
            QRect(r.left(), mid_y, size, size),  # W
            QRect(r.right() - size, mid_y, size, size),  # E
            QRect(mid_x, r.top(), size, size),  # N
            QRect(mid_x, r.bottom() - size, size, size),  # S
        ]
        
        for side in sides:
            painter.drawRect(side)

class OverlayAppUI(QWidget):
    def __init__(self):
        super().__init__()
        self.selection_overlay = SelectionOverlay()
        self.canvas_overlay = CanvasOverlay()

        self.setup_ui()
        self.connect_signals()

    def update_mode_controls(self):
        """Обновляет элементы управления в зависимости от выбранного режима"""
        mode = self.mode_combo.currentText()

        # Полностью очищаем layout, не удаляя виджеты
        while self.style_layout.count():
            item = self.style_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.hide()  # Скрываем, чтобы точно не было видимости
                self.style_layout.removeWidget(widget)

        # Создаём метку
        label = QLabel("Цвет:" if mode == "Цвет" else "Тип стекла:")
        label.setFixedWidth(80)
        combo = self.color_combo if mode == "Цвет" else self.glass_combo

        # Добавляем
        self.style_layout.addWidget(label)
        self.style_layout.addWidget(combo)

        # Показываем нужный комбобокс
        self.color_combo.show() if mode == "Цвет" else self.glass_combo.show()

        # Добавляем растяжку в конец (если нужно)
        self.style_layout.addStretch()


    def setup_ui(self):
        self.setWindowTitle("Shadow overlay")
        self.setGeometry(100, 100, 340, 150)
        layout = QVBoxLayout()

        controls = QHBoxLayout()
        self.btn_create = QPushButton("Создать область")
        self.btn_clear = QPushButton("Очистить всё")
        controls.addWidget(self.btn_create)
        controls.addWidget(self.btn_clear)

        mode_layout = QHBoxLayout()
        mode_layout.addWidget(QLabel("Режим:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Цвет", "Стекло"])
        self.mode_combo.currentTextChanged.connect(self.update_mode_controls)
        mode_layout.addWidget(self.mode_combo)

        self.style_layout = QHBoxLayout()  # Для цвета или стекла

        layout.addLayout(controls)
        layout.addLayout(mode_layout)
        layout.addLayout(self.style_layout)
        self.setLayout(layout)

        # Создаём комбобоксы
        self.color_combo = QComboBox()
        self.glass_combo = QComboBox()

        self.add_standard_colors()
        self.add_glass_options()

        # Инициализируем отображение
        self.update_mode_controls()

        # Подключаем сигналы
        self.btn_create.clicked.connect(self.start_selection)
        self.btn_clear.clicked.connect(self.clear_all)

    def add_standard_colors(self):
        """Добавляет стандартные цвета в выпадающий список"""
        from PyQt6.QtGui import QColor, QPixmap, QIcon
        
        colors = [
            ("Чёрный", QColor(0, 0, 0, 255)),
            ("Белый", QColor(255, 255, 255, 255)),
            ("Красный", QColor(255, 0, 0, 255)),
            ("Зеленый", QColor(0, 255, 0, 255)),
            ("Синий", QColor(0, 0, 255, 255)),
            ("Желтый", QColor(255, 255, 0, 255)),
            ("Голубой", QColor(0, 255, 255, 255)),
            ("Пурпурный", QColor(255, 0, 255, 255)),
            ("Серый", QColor(128, 128, 128, 255)),
            ("Темно-красный", QColor(139, 0, 0, 255)),
            ("Темно-зеленый", QColor(0, 100, 0, 255)),
            ("Темно-синий", QColor(0, 0, 139, 255)),
            ("Оранжевый", QColor(255, 165, 0, 255)),
            ("Розовый", QColor(255, 192, 203, 255)),
            ("Коричневый", QColor(165, 42, 42, 255)),
            ("Фиолетовый", QColor(128, 0, 128, 255)),
        ]
        
        for name, color in colors:
            icon_size = 20
            pixmap = QPixmap(icon_size, icon_size)
            pixmap.fill(color)
            icon = QIcon(pixmap)
            self.color_combo.addItem(icon, name, color)

    def add_glass_options(self):
        """Добавляет 4 типа визуальных эффектов: размытие, мозаика, Ч/Б"""
        from PyQt6.QtGui import QColor, QPixmap, QIcon

        effects = [
            ("Размытие: Слабое", "blur", 2),
            ("Размытие: Сильное", "blur", 12),
            ("Мозаика", "mosaic", 8),         # размер блока для мозаики
            ("Ч/Б", "grayscale", None),       # без параметра
        ]

        for name, effect_type, param in effects:
            # Создаём превью — просто серый квадрат
            pixmap = QPixmap(20, 20)
            pixmap.fill(QColor(200, 200, 200))
            painter = QPainter(pixmap)
            pen = QPen(QColor(100, 100, 100), 1)
            painter.setPen(pen)
            painter.drawRect(0, 0, 19, 19)

            # Можно добавить символ или штриховку для разных типов
            if effect_type == "mosaic":
                painter.fillRect(2, 2, 4, 4, QColor(180, 180, 180))
                painter.fillRect(10, 10, 4, 4, QColor(180, 180, 180))
            elif effect_type == "grayscale":
                painter.setBrush(QBrush(QColor(100, 100, 100)))
                painter.drawRect(2, 2, 16, 16)

            painter.end()

            icon = QIcon(pixmap)
            self.glass_combo.addItem(icon, name, (effect_type, param))  # Сохраняем тип и параметр

    def connect_signals(self):
        """Подключает сигналы"""
        self.selection_overlay.area_selected.connect(self.on_area_selected)

    def start_selection(self):
        """Начинает процесс выделения области"""
        logging.info("Режим выделения активирован")
        self.hide()
        self.selection_overlay.show()
        self.selection_overlay.raise_()

    def on_area_selected(self, x, y, w, h):
        """Обработчик создания новой области"""
        mode = self.mode_combo.currentText()

        if mode == "Цвет":
            index = self.color_combo.currentIndex()
            color = self.color_combo.itemData(index)
            self.canvas_overlay.add_rectangle(x, y, w, h, color=color, mode="solid")
        else:  # "Стекло"
            data = self.glass_combo.currentData()  # (effect_type, param)
            if data is None:
                effect_type, effect_param = "blur", 8
            else:
                effect_type, effect_param = data

            self.canvas_overlay.add_rectangle(
                x, y, w, h,
                mode="frosted",
                effect_type=effect_type,
                effect_param=effect_param
            )

        self.canvas_overlay.show()
        self.canvas_overlay.raise_()
        logging.info(f"Прямоугольник создан: {x}, {y}, {w}, {h}, режим: {mode}")
        self.show()

    def clear_all(self):
        """Очищает все прямоугольники"""
        self.canvas_overlay.rectangles.clear()
        self.canvas_overlay.update()
        logging.info("Все прямоугольники удалены")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = OverlayAppUI()
    window.show()
    sys.exit(app.exec())