import sys
import os
from PIL import Image
import pyperclip
import re
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
    QPushButton, QFileDialog, QProgressBar, QScrollArea, QGroupBox, QRadioButton,
    QButtonGroup, QSpinBox, QMessageBox, QTextEdit, QFrame, QColorDialog,
    QDialog, QDialogButtonBox, QCheckBox
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal

from PyQt5.QtGui import QColor, QPalette, QFont, QPixmap, QPainter, QImage, QIcon

class ImageProcessorThread(QThread):
    progress_updated = pyqtSignal(int)
    processing_finished = pyqtSignal(list, list, list, int, QImage, int, int, int, int)
    error_occurred = pyqtSignal(str)
    
    def __init__(self, image_path, target_width, target_height, transparent_option, custom_color, 
                 font_size, segment_rule, pixel_segment_size, char_segment_size):
        super().__init__()
        self.image_path = image_path
        self.target_w = target_width
        self.target_h = target_height
        self.trans_opt = transparent_option  # 0=自定义背景色，1=保持透明
        self.custom_color = custom_color     # 自定义背景色（十六进制）
        self.font_size = font_size
        self.segment_rule = segment_rule
        self.pixel_limit = max(pixel_segment_size, 1)
        self.char_limit = max(char_segment_size, 1)
        
        self.font_start = f'<size={self.font_size}>' if self.font_size > 0 else ''
        self.font_end = '</size>' if self.font_size > 0 else ''
        self.font_tag_len = len(self.font_start) + len(self.font_end)
        
        self.has_alpha = False
        self.total_pixels = 0
        self.current_x = 0
        self.current_y = 0
        self.total_processed_pixels = 0

    def run(self):
        try:
            # 图片读取与处理
            img = Image.open(self.image_path)
            if img.format.lower() in ['jpg', 'jpeg'] or img.mode != 'RGBA':
                img = img.convert('RGB')
                self.has_alpha = False
            else:
                img = img.convert('RGBA')
                self.has_alpha = True
            
            orig_w, orig_h = img.size
            if self.target_w > 0 and self.target_h > 0:
                img = img.resize((self.target_w, self.target_h), Image.LANCZOS)
            self.img_w, self.img_h = img.size
            self.pixels = img.load()
            self.total_pixels = self.img_w * self.img_h
            
            # 初始化预览图
            self.preview = QImage(self.img_w, self.img_h, QImage.Format_ARGB32)
            self.preview.fill(Qt.white if not self.has_alpha else Qt.transparent)
            
            final_segments = []
            line_end_markers = []
            segment_line_mapping = []
            self.current_x = 0
            self.current_y = 0
            self.total_processed_pixels = 0
            
            # 循环处理所有像素（逐行处理，确保行完整性）
            while self.current_y < self.img_h:
                current_line = self.current_y + 1
                # 按行处理，确保当前行所有像素都被处理
                while self.current_x < self.img_w:
                    if self.segment_rule == 0:
                        segment, is_line_end, pixel_count_in_seg = self.process_pixel_segment()
                    else:
                        # 字符分段：严格控制字符长度，确保不丢像素
                        segment, is_line_end, pixel_count_in_seg = self.process_char_segment_line_safe()
                    
                    if segment:
                        final_segments.append(segment)
                        segment_line_mapping.append(current_line)
                        self.total_processed_pixels += pixel_count_in_seg
                    
                    # 字符分段的is_line_end仅表示当前段是否超字符限制，不代表行结束
                    if self.segment_rule == 1:
                        is_line_end = (self.current_x >= self.img_w)
                
                # 当前行处理完毕，标记行尾段
                if final_segments:
                    line_end_markers.append(len(final_segments) - 1)
                # 换行
                self.current_x = 0
                self.current_y += 1
                
                # 更新进度
                processed = self.current_y * self.img_w + self.current_x
                progress = int((processed / self.total_pixels) * 100)
                self.progress_updated.emit(progress)
            
            self.progress_updated.emit(100)
            self.processing_finished.emit(
                final_segments, line_end_markers, segment_line_mapping, self.total_processed_pixels,
                self.preview, self.img_w, self.img_h, orig_w, orig_h
            )
            
        except Exception as e:
            self.error_occurred.emit(f"处理失败：{str(e)}")
    
    def process_char_segment_line_safe(self):
        """字符分段处理"""
        segment_parts = [self.font_start]
        current_char_count = self.font_tag_len
        current_color_tag = None
        current_color_pixel_count = 0
        pixel_count_in_seg = 0
        is_line_end = False
        
        # 处理当前位置开始的像素，直到：1）超字符限制；2）行结束
        while self.current_x < self.img_w:
            # 获取当前像素
            if self.has_alpha:
                r, g, b, a = self.pixels[self.current_x, self.current_y]
            else:
                r, g, b = self.pixels[self.current_x, self.current_y]
                a = 255
            
            if self.trans_opt == 0 and a < 128:  # 自定义背景色 + 像素透明
                color_tag = self.get_custom_color_tag()
            else:
                color_tag = self.get_original_color_tag(r, g, b, a)
            
            # 计算添加当前像素后的字符长度
            if color_tag == current_color_tag:
                new_char_count = current_char_count + 1  # 同色仅+1（█）
            else:
                # 不同色：+ 颜色标签长度 + 1（█） + 闭合标签长度
                new_char_count = current_char_count + len(color_tag) + 1 + len('</color>')
            
            # 若超字符限制，停止当前段（当前像素不加入，留到下一段）
            if new_char_count > self.char_limit:
                # 若当前段为空（第一个像素就超限制），强制加入（避免空段）
                if pixel_count_in_seg == 0:
                    segment_parts.append(f"{color_tag}█</color>")
                    segment_parts.append(self.font_end)
                    pixel_count_in_seg = 1
                    self.update_preview(self.current_x, self.current_y, r, g, b, a)
                    self.current_x += 1
                break
            
            # 同色合并处理
            if color_tag == current_color_tag:
                current_color_pixel_count += 1
            else:
                # 先添加之前的颜色块
                if current_color_tag is not None:
                    segment_parts.append(f"{current_color_tag}{'█' * current_color_pixel_count}</color>")
                current_color_tag = color_tag
                current_color_pixel_count = 1
            
            # 更新状态
            current_char_count = new_char_count
            pixel_count_in_seg += 1
            self.update_preview(self.current_x, self.current_y, r, g, b, a)
            self.current_x += 1
        
        # 添加最后一个颜色块和字体结束标签
        if current_color_tag is not None and pixel_count_in_seg > 0:
            segment_parts.append(f"{current_color_tag}{'█' * current_color_pixel_count}</color>")
        segment_parts.append(self.font_end)
        
        # 行结束判断
        is_line_end = (self.current_x >= self.img_w)
        
        return ''.join(segment_parts), is_line_end, pixel_count_in_seg

    def get_original_color_tag(self, r, g, b, a):
        """生成原始颜色标签（透明RRGGBBAA，非透明RRGGBB）"""
        if not self.has_alpha:
            return f'<color=#{r:02x}{g:02x}{b:02x}>'
        return f'<color=#{r:02x}{g:02x}{b:02x}{a:02x}>' if a < 128 else f'<color=#{r:02x}{g:02x}{b:02x}>'
    
    def get_custom_color_tag(self):
        """生成自定义背景色的颜色标签"""
        return f'<color={self.custom_color}>'

    def process_pixel_segment(self):
        """按像素分段"""
        segment_parts = [self.font_start]
        current_pixel_count = 0
        is_line_end = False
        current_color_tag = None
        color_pixel_count = 0
        
        while self.current_y < self.img_h and current_pixel_count < self.pixel_limit:
            if self.current_x >= self.img_w:
                is_line_end = True
                break
            
            # 获取像素
            if self.has_alpha:
                r, g, b, a = self.pixels[self.current_x, self.current_y]
            else:
                r, g, b = self.pixels[self.current_x, self.current_y]
                a = 255
            
            if self.trans_opt == 0 and a < 128:  # 自定义背景色 + 像素透明
                color_tag = self.get_custom_color_tag()
            else:
                color_tag = self.get_original_color_tag(r, g, b, a)
            
            if color_tag == current_color_tag:
                color_pixel_count += 1
            else:
                if current_color_tag is not None:
                    segment_parts.append(f"{current_color_tag}{'█' * color_pixel_count}</color>")
                current_color_tag = color_tag
                color_pixel_count = 1
            
            self.update_preview(self.current_x, self.current_y, r, g, b, a)
            self.current_x += 1
            current_pixel_count += 1
            
            # 行尾判断
            if self.current_x >= self.img_w:
                is_line_end = True
                break
        
        # 添加最后一个颜色块
        if current_color_tag is not None:
            segment_parts.append(f"{current_color_tag}{'█' * color_pixel_count}</color>")
        segment_parts.append(self.font_end)
        
        return ''.join(segment_parts), is_line_end, current_pixel_count
    
    def update_preview(self, x, y, r, g, b, a):
        """更新预览图像素"""
        if self.trans_opt == 1 and a < 128:
            self.preview.setPixelColor(x, y, QColor(r, g, b, a))
        elif self.trans_opt == 0 and a < 128:
            self.preview.setPixelColor(x, y, QColor(self.custom_color))
        else:
            self.preview.setPixelColor(x, y, QColor(r, g, b))

class PreviewDialog(QDialog):
    def __init__(self, preview_img, target_w, target_h, orig_w, orig_h, parent=None):
        super().__init__(parent)
        self.setWindowTitle("预览效果")
        self.resize(700, 600)
        
        layout = QVBoxLayout(self)
        info = QLabel(f"目标分辨率: {target_w}x{target_h} | 原图分辨率: {orig_w}x{orig_h}")
        info.setStyleSheet("color: #00c8ff; font-weight: bold;")
        layout.addWidget(info)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setAlignment(Qt.AlignCenter)
        
        self.img_label = QLabel()
        self.img_label.setAlignment(Qt.AlignCenter)
        scale = min(500/target_w, 500/target_h, 20.0)
        self.scale_preview(preview_img, target_w, target_h, int(scale))
        
        container_layout.addWidget(self.img_label)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)
        
        # 缩放控制
        scale_layout = QHBoxLayout()
        self.scale_slider = QSpinBox()
        self.scale_slider.setRange(1, 20)
        self.scale_slider.setValue(int(scale))
        self.scale_slider.setSuffix("x")
        self.scale_slider.valueChanged.connect(lambda v: self.scale_preview(preview_img, target_w, target_h, v))
        scale_layout.addWidget(QLabel("缩放:"))
        scale_layout.addWidget(self.scale_slider)
        scale_layout.addStretch()
        layout.addLayout(scale_layout)
        
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok)
        btn_box.accepted.connect(self.accept)
        layout.addWidget(btn_box)
    
    def scale_preview(self, img, w, h, scale):
        """缩放预览图"""
        sw = int(w * scale)
        sh = int(h * scale)
        scaled = img.scaled(sw, sh, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.img_label.setPixmap(QPixmap.fromImage(scaled))

class ImageToRichTextApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("图片转富文本工具")
        self.setGeometry(100, 100, 850, 750)
        self.setMinimumSize(600, 500)
        
        # 打包时设置图标
        # self.setWindowIcon(QIcon(os.path.join(sys._MEIPASS, "千星图标.png")))

        # 初始化变量
        self.custom_color = "#888888"
        self.results = []
        self.line_end_markers = []
        self.segment_line_mapping = []
        self.total_pixel_count = 0
        self.preview_img = None
        self.current_page = 0
        self.total_pages = 0
        self.items_per_page = 5
        self.original_width = 0
        self.original_height = 0
        self.target_width = 40
        self.target_height = 30
        
        # 初始化界面
        self.init_style()
        self.init_ui()
    
    def init_style(self):
        """深色主题样式"""
        self.setStyleSheet("""
            QMainWindow { background-color: #2d2d30; color: #ffffff; }
            QGroupBox {
                border: 1px solid #3f3f46; border-radius: 5px;
                margin-top: 1ex; font-weight: bold; color: #e0e0e0; padding: 5px;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
            QLabel { color: #e0e0e0; }
            QLineEdit, QSpinBox {
                background-color: #3f3f46; border: 1px solid #515151;
                border-radius: 4px; padding: 4px; color: #ffffff;
            }
            QPushButton {
                background-color: #0078d4; color: white; border: none;
                border-radius: 4px; padding: 6px 12px; font-weight: bold; min-width: 80px;
            }
            QPushButton:hover { background-color: #106ebe; }
            QPushButton:disabled { background-color: #555555; }
            QRadioButton, QCheckBox { color: #e0e0e0; }
            QProgressBar {
                border: 1px solid #515151; border-radius: 4px;
                text-align: center; background-color: #3f3f46; height: 20px;
            }
            QProgressBar::chunk { background-color: #0078d4; width: 10px; }
            QTextEdit {
                background-color: #1e1e1e; color: #d4d4d4;
                border: 1px solid #3f3f46; border-radius: 4px; padding: 5px;
            }
            QScrollArea { border: none; background-color: transparent; }
            QFrame#segmentFrame {
                background-color: #3f3f46; border: 1px solid #515151;
                border-radius: 4px; padding: 8px;
            }
            QPushButton#copyBtn { background-color: #3a963a; min-width: 60px; }
            QPushButton#copyBtn:hover { background-color: #2d7a2d; }
            QPushButton#navBtn { background-color: #5e5e5e; min-width: 60px; }
            QPushButton#navBtn:hover { background-color: #6e6e6e; }
            QFrame#pageNav { background-color: #3a3a3a; border-radius: 4px; padding: 5px; }
            QLabel#lineEndMarker { color: #ff6b6b; font-weight: bold; font-size: 14px; }
            QLabel#pixelCountLabel { color: #00ff9d; font-style: italic; }
            QLabel#charCountLabel { color: #00c8ff; font-style: italic; }
            QLabel#segmentSeqLabel { color: #ffff6b; font-weight: bold; }
        """)
    
    def init_ui(self):
        """构建界面"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)
        
        # 标题
        title_label = QLabel("图片转富文本工具")
        title_label.setStyleSheet("font-size: 24px; font-weight: bold; color: #00ffcc; margin-bottom: 15px;")
        title_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title_label)
        
        # 滚动容器
        scroll_container = QScrollArea()
        scroll_container.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(15)
        scroll_container.setWidget(scroll_content)
        main_layout.addWidget(scroll_container, 1)
        
        # 1. 图片选择
        image_group = QGroupBox("1. 选择图片")
        image_layout = QHBoxLayout()
        self.image_path_edit = QLineEdit()
        self.image_path_edit.setPlaceholderText("请选择图片文件...")
        self.image_path_edit.setReadOnly(True)
        browse_btn = QPushButton("浏览...")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(self.browse_img)
        image_layout.addWidget(self.image_path_edit, 4)
        image_layout.addWidget(browse_btn, 1)
        image_group.setLayout(image_layout)
        scroll_layout.addWidget(image_group)
        
        # 2. 分辨率设置
        resolution_group = QGroupBox("2. 分辨率设置")
        resolution_layout = QVBoxLayout()
        self.original_resolution_label = QLabel("原图分辨率: 未选择图片")
        resolution_layout.addWidget(self.original_resolution_label)
        
        target_layout = QHBoxLayout()
        target_layout.addWidget(QLabel("目标分辨率:"))
        self.target_width_spin = QSpinBox()
        self.target_width_spin.setRange(1, 9999)
        self.target_width_spin.setValue(40)
        self.target_width_spin.setFixedWidth(80)
        self.target_height_spin = QSpinBox()
        self.target_height_spin.setRange(1, 9999)
        self.target_height_spin.setValue(30)
        self.target_height_spin.setFixedWidth(80)
        target_layout.addWidget(self.target_width_spin)
        target_layout.addWidget(QLabel("X"))
        target_layout.addWidget(self.target_height_spin)
        target_layout.addStretch()
        
        self.keep_aspect_checkbox = QCheckBox("保持宽高比")
        self.keep_aspect_checkbox.setChecked(True)
        self.target_width_spin.valueChanged.connect(self.on_resolution_changed)
        self.target_height_spin.valueChanged.connect(self.on_resolution_changed)
        
        resolution_layout.addLayout(target_layout)
        resolution_layout.addWidget(self.keep_aspect_checkbox)
        resolution_group.setLayout(resolution_layout)
        scroll_layout.addWidget(resolution_group)
        
        # 3. 透明像素处理
        transparent_group = QGroupBox("3. 透明像素处理")
        transparent_layout = QVBoxLayout()
        self.transparent_bg = QButtonGroup(self)
        
        # 保持透明选项，默认勾选
        self.keep_transparent_radio = QRadioButton("保持透明")
        self.keep_transparent_radio.setChecked(True)
        transparent_layout.addWidget(self.keep_transparent_radio)
        
        # 自定义背景色
        color_btn_layout = QHBoxLayout()
        self.custom_color_radio = QRadioButton("自定义背景色:")
        color_btn_layout.addWidget(self.custom_color_radio)
        self.color_button = QPushButton(self.custom_color)
        self.color_button.setFixedWidth(80)
        self.color_button.clicked.connect(self.choose_color)
        color_btn_layout.addWidget(self.color_button)
        color_btn_layout.addStretch()
        transparent_layout.addLayout(color_btn_layout)
        
        self.transparent_bg.addButton(self.keep_transparent_radio, 1)
        self.transparent_bg.addButton(self.custom_color_radio, 0)
        transparent_group.setLayout(transparent_layout)
        scroll_layout.addWidget(transparent_group)
        
        # 4. 分段规则（像素默认40，字符默认1000）
        segment_group = QGroupBox("4. 分段规则")
        segment_layout = QVBoxLayout()
        self.segment_rule_group = QButtonGroup(self)
        self.segment_rule_group.setExclusive(True)
        
        # 按像素分段（默认40）
        pixel_layout = QHBoxLayout()
        self.pixel_segment_radio = QRadioButton("按像素数量分段（每段最多:）")
        self.pixel_segment_radio.setChecked(True)
        self.pixel_segment_spin = QSpinBox()
        self.pixel_segment_spin.setRange(1, 99999)
        self.pixel_segment_spin.setValue(40)  # 像素默认40
        self.pixel_segment_spin.setFixedWidth(80)
        pixel_layout.addWidget(self.pixel_segment_radio)
        pixel_layout.addWidget(self.pixel_segment_spin)
        pixel_layout.addStretch()
        
        # 按字符分段（默认1000）
        char_layout = QHBoxLayout()
        self.char_segment_radio = QRadioButton("按字符长度分段（每段最多:）")
        self.char_segment_spin = QSpinBox()
        self.char_segment_spin.setRange(1, 99999)
        self.char_segment_spin.setValue(1000)  # 字符默认1000
        self.char_segment_spin.setFixedWidth(80)
        char_layout.addWidget(self.char_segment_radio)
        char_layout.addWidget(self.char_segment_spin)
        char_layout.addStretch()
        
        segment_layout.addLayout(pixel_layout)
        segment_layout.addLayout(char_layout)
        self.segment_rule_group.addButton(self.pixel_segment_radio, 0)
        self.segment_rule_group.addButton(self.char_segment_radio, 1)
        segment_group.setLayout(segment_layout)
        scroll_layout.addWidget(segment_group)
        
        # 5. 文本样式设置（默认勾选，默认值5）
        font_group = QGroupBox("5. 文本样式设置")
        font_layout = QHBoxLayout()
        self.use_font_size_checkbox = QCheckBox("设置文本大小:")
        self.use_font_size_checkbox.setChecked(True)  # 默认勾选
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(1, 100)
        self.font_size_spin.setValue(5)  # 默认值5
        self.font_size_spin.setEnabled(True)  # 勾选后启用
        self.font_size_spin.setFixedWidth(60)
        # 勾选状态变化时控制SpinBox启用/禁用
        self.use_font_size_checkbox.toggled.connect(self.font_size_spin.setEnabled)
        font_layout.addWidget(self.use_font_size_checkbox)
        font_layout.addWidget(self.font_size_spin)
        font_layout.addStretch()
        font_group.setLayout(font_layout)
        scroll_layout.addWidget(font_group)
        
        # 6. 操作按钮
        button_group = QGroupBox("6. 操作")
        button_layout = QVBoxLayout()
        btn_layout = QHBoxLayout()
        self.process_btn = QPushButton("开始生成")
        self.process_btn.clicked.connect(self.process_img)
        self.preview_btn = QPushButton("预览结果")
        self.preview_btn.setEnabled(False)
        self.preview_btn.clicked.connect(self.show_preview)
        btn_layout.addWidget(self.process_btn)
        btn_layout.addWidget(self.preview_btn)
        btn_layout.addStretch()
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        button_layout.addLayout(btn_layout)
        button_layout.addWidget(self.progress_bar)
        button_group.setLayout(button_layout)
        scroll_layout.addWidget(button_group)
        
        # 7. 结果展示
        result_group = QGroupBox("7. 处理结果")
        result_layout = QVBoxLayout()
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setMinimumHeight(300)
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(10, 10, 10, 10)
        self.scroll_layout.setSpacing(10)
        self.scroll_area.setWidget(self.scroll_content)
        result_layout.addWidget(self.scroll_area)
        
        # 分页导航
        nav_frame = QFrame()
        nav_frame.setObjectName("pageNav")
        nav_layout = QHBoxLayout(nav_frame)
        self.prev_btn = QPushButton("上一页")
        self.prev_btn.setObjectName("navBtn")
        self.prev_btn.setEnabled(False)
        self.prev_btn.clicked.connect(self.prev_page)
        self.page_label = QLabel("第 0 页，共 0 页")
        self.page_label.setAlignment(Qt.AlignCenter)
        self.next_btn = QPushButton("下一页")
        self.next_btn.setObjectName("navBtn")
        self.next_btn.setEnabled(False)
        self.next_btn.clicked.connect(self.next_page)
        nav_layout.addWidget(self.prev_btn)
        nav_layout.addWidget(self.page_label, 2)
        nav_layout.addWidget(self.next_btn)
        result_layout.addWidget(nav_frame)
        result_group.setLayout(result_layout)
        main_layout.addWidget(result_group)
    
    def on_resolution_changed(self, value):
        """保持宽高比"""
        if not self.keep_aspect_checkbox.isChecked() or self.original_width == 0 or self.original_height == 0:
            return
        
        sender = self.sender()
        if sender == self.target_width_spin:
            new_width = self.target_width_spin.value()
            new_height = int(new_width * self.original_height / self.original_width)
            self.target_height_spin.blockSignals(True)
            self.target_height_spin.setValue(new_height)
            self.target_height_spin.blockSignals(False)
        elif sender == self.target_height_spin:
            new_height = self.target_height_spin.value()
            new_width = int(new_height * self.original_width / self.original_height)
            self.target_width_spin.blockSignals(True)
            self.target_width_spin.setValue(new_width)
            self.target_width_spin.blockSignals(False)
        
        self.target_width = self.target_width_spin.value()
        self.target_height = self.target_height_spin.value()
    
    def browse_img(self):
        """选择图片"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择图片", "", "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif *.tiff)"
        )
        if file_path:
            self.image_path_edit.setText(file_path)
            try:
                with Image.open(file_path) as img:
                    self.original_width, self.original_height = img.size
                    self.original_resolution_label.setText(f"原图分辨率: {self.original_width} x {self.original_height}")
                    default_width = min(self.original_width, 40)
                    default_height = int(default_width * self.original_height / self.original_width)
                    self.target_width_spin.blockSignals(True)
                    self.target_height_spin.blockSignals(True)
                    self.target_width_spin.setValue(default_width)
                    self.target_height_spin.setValue(default_height)
                    self.target_width_spin.blockSignals(False)
                    self.target_height_spin.blockSignals(False)
                    self.target_width = default_width
                    self.target_height = default_height
            except Exception as e:
                QMessageBox.critical(self, "错误", f"读取图片失败：{str(e)}")
    
    def choose_color(self):
        """选择自定义背景色"""
        color = QColorDialog.getColor(QColor(self.custom_color), self, "选择背景颜色")
        if color.isValid():
            self.custom_color = color.name()
            self.color_button.setText(self.custom_color)
    
    def process_img(self):
        """开始处理"""
        image_path = self.image_path_edit.text()
        if not image_path or not os.path.exists(image_path):
            QMessageBox.warning(self, "警告", "请先选择有效的图片文件")
            return
        
        # 获取透明处理选项（1=保持透明，0=自定义背景）
        transparent_option = 1 if self.keep_transparent_radio.isChecked() else 0
        # 获取分段规则（0=按像素，1=按字符）
        segment_rule = 0 if self.pixel_segment_radio.isChecked() else 1
        pixel_segment_size = self.pixel_segment_spin.value()
        char_segment_size = self.char_segment_spin.value()
        font_size = self.font_size_spin.value() if self.use_font_size_checkbox.isChecked() else 0
        
        # 清空结果
        self.clear_results()
        
        # 创建线程
        self.thread = ImageProcessorThread(
            image_path, self.target_width, self.target_height, transparent_option,
            self.custom_color, font_size, segment_rule, pixel_segment_size, char_segment_size
        )
        self.thread.progress_updated.connect(self.update_progress)
        self.thread.processing_finished.connect(self.on_finish)
        self.thread.error_occurred.connect(self.on_error)
        
        # 禁用按钮
        self.process_btn.setEnabled(False)
        self.preview_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.thread.start()
    
    def update_progress(self, value):
        """更新进度条"""
        self.progress_bar.setValue(value)
        self.progress_bar.setFormat(f"处理中: {value}%")
    
    def on_finish(self, segments, line_end_markers, segment_line_mapping, total_pixel_count, preview_image, width, height, original_width, original_height):
        """处理完成"""
        self.results = segments
        self.line_end_markers = line_end_markers
        self.segment_line_mapping = segment_line_mapping
        self.total_pixel_count = total_pixel_count
        self.preview_img = preview_image
        self.target_width = width
        self.target_height = height
        self.original_width = original_width
        self.original_height = original_height
        
        # 分页计算
        self.total_pages = (len(self.results) + self.items_per_page - 1) // self.items_per_page
        self.current_page = 0
        
        # 更新界面
        self.preview_btn.setEnabled(True)
        self.prev_btn.setEnabled(False)
        self.next_btn.setEnabled(self.total_pages > 1)
        self.page_label.setText(f"第 {self.current_page + 1} 页，共 {self.total_pages} 页")
        self.display_page(0)
        
        # 恢复按钮
        self.process_btn.setEnabled(True)
        rule_name = "按像素分段" if self.pixel_segment_radio.isChecked() else "按字符分段"
        QMessageBox.information(self, "处理完成", f"共生成 {len(self.results)} 段富文本（{rule_name}），总计 {total_pixel_count} 个像素（应等于 {width * height}）")
    
    def display_page(self, page_number):
        """显示当前页结果（「第X行第Y段」序号）"""
        self.clear_results()
        if not self.results:
            return
        
        start_idx = page_number * self.items_per_page
        end_idx = min(start_idx + self.items_per_page, len(self.results))
        
        # 计算每行的段落序号
        line_segment_count = {}
        line_segment_index = {}
        for seg_idx, line_num in enumerate(self.segment_line_mapping):
            if line_num not in line_segment_count:
                line_segment_count[line_num] = 0
            line_segment_count[line_num] += 1
            line_segment_index[seg_idx] = line_segment_count[line_num]
        
        for i in range(start_idx, end_idx):
            segment = self.results[i]
            line_num = self.segment_line_mapping[i]
            seg_in_line_idx = line_segment_index[i]
            
            # 统计信息
            clean_text = re.sub(r'<[^>]+>', '', segment)
            pixel_count = clean_text.count('█')
            char_count = len(segment)
            is_line_end = i in self.line_end_markers
            
            # 段落容器
            segment_frame = QFrame()
            segment_frame.setObjectName("segmentFrame")
            segment_layout = QVBoxLayout(segment_frame)
            
            # 头部（序号+行尾+统计）
            header_layout = QHBoxLayout()
            seq_label = QLabel(f"第{line_num}行第{seg_in_line_idx}段")
            seq_label.setObjectName("segmentSeqLabel")
            header_layout.addWidget(seq_label)
            if is_line_end:
                end_marker = QLabel("【行尾】")
                end_marker.setObjectName("lineEndMarker")
                header_layout.addWidget(end_marker)
            pixel_label = QLabel(f"像素数: {pixel_count}")
            pixel_label.setObjectName("pixelCountLabel")
            header_layout.addWidget(pixel_label)
            char_label = QLabel(f"字符数: {char_count}")
            char_label.setObjectName("charCountLabel")
            header_layout.addWidget(char_label)
            header_layout.addStretch()
            segment_layout.addLayout(header_layout)
            
            # 内容
            content_edit = QTextEdit()
            content_edit.setPlainText(segment)
            content_edit.setReadOnly(True)
            content_edit.setMaximumHeight(100)
            segment_layout.addWidget(content_edit)
            
            # 复制按钮
            copy_btn = QPushButton("复制")
            copy_btn.setObjectName("copyBtn")
            copy_btn.clicked.connect(lambda _, t=segment, line=line_num, seg=seg_in_line_idx: self.copy_segment(t, line, seg))
            btn_layout = QHBoxLayout()
            btn_layout.addStretch()
            btn_layout.addWidget(copy_btn)
            segment_layout.addLayout(btn_layout)
            
            self.scroll_layout.addWidget(segment_frame)
        
        self.scroll_layout.addStretch()
        self.prev_btn.setEnabled(page_number > 0)
        self.next_btn.setEnabled(page_number < self.total_pages - 1)
        self.page_label.setText(f"第 {page_number + 1} 页，共 {self.total_pages} 页")
    
    def clear_results(self):
        """清空结果区域"""
        while self.scroll_layout.count() > 0:
            item = self.scroll_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
    
    def copy_segment(self, text, line_num, seg_in_line_idx):
        """复制段落"""
        pyperclip.copy(text)
        QMessageBox.information(self, "复制成功", f"第{line_num}行第{seg_in_line_idx}段已复制到剪贴板")
    
    def show_preview(self):
        """显示预览图"""
        if not self.preview_img:
            QMessageBox.warning(self, "警告", "请先生成处理结果")
            return
        
        preview_dialog = PreviewDialog(
            self.preview_img, self.target_width, self.target_height,
            self.original_width, self.original_height, self
        )
        preview_dialog.exec_()
    
    def prev_page(self):
        """上一页"""
        if self.current_page > 0:
            self.current_page -= 1
            self.display_page(self.current_page)
    
    def next_page(self):
        """下一页"""
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self.display_page(self.current_page)
    
    def on_error(self, msg):
        """错误处理"""
        QMessageBox.critical(self, "处理错误", msg)
        self.process_btn.setEnabled(True)
        self.progress_bar.setValue(0)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    # 全局调色板
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor("#2d2d30"))
    palette.setColor(QPalette.WindowText, QColor(224, 224, 224))
    palette.setColor(QPalette.Base, QColor("#1e1e1e"))
    palette.setColor(QPalette.AlternateBase, QColor("#2d2d30"))
    palette.setColor(QPalette.Text, QColor(224, 224, 224))
    palette.setColor(QPalette.Button, QColor(63, 63, 70))
    palette.setColor(QPalette.ButtonText, Qt.white)
    palette.setColor(QPalette.Highlight, QColor(0, 120, 212).lighter())
    palette.setColor(QPalette.HighlightedText, Qt.white)
    app.setPalette(palette)
    
    # 全局字体
    app.setFont(QFont("Microsoft YaHei", 9))
    
    window = ImageToRichTextApp()
    window.show()
    sys.exit(app.exec_())