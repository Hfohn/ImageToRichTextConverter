import sys
import os
from PIL import Image
import pyperclip
import re
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
    QPushButton, QFileDialog, QProgressBar, QScrollArea, QGroupBox, QRadioButton,
    QButtonGroup, QSpinBox, QMessageBox, QTextEdit, QFrame, QColorDialog,
    QDialog, QDialogButtonBox, QCheckBox, QSlider
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QColor, QPalette, QFont, QPixmap, QPainter, QImage, QIcon

class ImageProcessorThread(QThread):
    progress_updated = pyqtSignal(int)
    processing_finished = pyqtSignal(list, list, list, int, QImage, int, int, int, int, list)
    error_occurred = pyqtSignal(str)
    
    def __init__(self, image_path, target_width, target_height, transparent_option, custom_color, 
                 font_size, segment_rule, pixel_segment_size, char_segment_size,
                 minimal_color, merge_similar, similarity_threshold, alpha_threshold, 
                 keep_above_alpha, space_replacement_enabled, space_count):
        super().__init__()
        self.image_path = image_path
        self.target_w = target_width
        self.target_h = target_height
        self.trans_opt = transparent_option  # 0=自定义背景色，1=保持透明，2=全角空格替代
        self.custom_color = custom_color     # 自定义背景色（十六进制）
        self.font_size = font_size
        self.segment_rule = segment_rule
        self.pixel_limit = max(pixel_segment_size, 1)
        self.char_limit = max(char_segment_size, 1)
        self.minimal_color = minimal_color  # 极简色彩开关
        self.merge_similar = merge_similar  # 相近颜色合并开关
        self.similarity_threshold = similarity_threshold  # 颜色相似度阈值
        self.alpha_threshold = alpha_threshold  # 半透明阈值（0-255）
        self.keep_above_alpha = keep_above_alpha  # True=保留高于阈值半透明，False=丢弃
        self.space_replacement_enabled = space_replacement_enabled  # 是否启用空格替代
        self.space_count = max(space_count, 1)  # 每个像素的全角空格数
        
        self.font_start = f'<size={self.font_size}>' if self.font_size > 0 else ''
        self.font_end = '</size>' if self.font_size > 0 else ''
        self.font_tag_len = len(self.font_start) + len(self.font_end)
        
        self.has_alpha = False
        self.total_pixels = 0
        self.current_x = 0
        self.current_y = 0
        self.total_processed_pixels = 0
        
        # 记录每行的分段序号
        self.line_segment_counter = {}  # key: 行号, value: 当前分段序号

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
            self.preview.fill(Qt.transparent)
            
            final_segments = []
            line_end_markers = []
            segment_line_mapping = []
            self.current_x = 0
            self.current_y = 0
            self.total_processed_pixels = 0
            self.line_segment_counter = {}  # 重置分段计数器
            segment_labels = []  # 存储每个分段的标签（如"第1行第1段"）
            
            # 循环处理所有像素（逐行处理，确保行完整性）
            while self.current_y < self.img_h:
                current_line = self.current_y + 1
                # 初始化当前行的分段计数器
                if current_line not in self.line_segment_counter:
                    self.line_segment_counter[current_line] = 0
                # 按行处理，确保当前行所有像素都被处理
                while self.current_x < self.img_w:
                    self.line_segment_counter[current_line] += 1  # 分段序号自增
                    current_segment_idx = self.line_segment_counter[current_line]
                    segment_label = f"第{current_line}行第{current_segment_idx}段"  # 生成标签
                    
                    if self.segment_rule == 0:
                        segment, is_line_end, pixel_count_in_seg = self.process_pixel_segment()
                    else:
                        # 字符分段：严格控制字符长度，确保不丢像素
                        segment, is_line_end, pixel_count_in_seg = self.process_char_segment_line_safe()
                    
                    if segment:
                        final_segments.append(segment)
                        segment_line_mapping.append(current_line)
                        segment_labels.append(segment_label)  # 保存标签
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
                self.preview, self.img_w, self.img_h, orig_w, orig_h, segment_labels  # 传递标签列表
            )
            
        except Exception as e:
            self.error_occurred.emit(f"处理失败：{str(e)}")
    
    def are_colors_similar(self, color1, color2):
        """判断两个颜色是否相似"""
        if not self.merge_similar:
            return False
            
        # 计算RGB三个通道的差异
        r_diff = abs(color1[0] - color2[0])
        g_diff = abs(color1[1] - color2[1])
        b_diff = abs(color1[2] - color2[2])
        
        # 计算总差异
        total_diff = r_diff + g_diff + b_diff
        
        # 如果有透明度通道，也考虑透明度差异
        if len(color1) == 4 and len(color2) == 4:
            a_diff = abs(color1[3] - color2[3])
            total_diff += a_diff
            
        return total_diff <= self.similarity_threshold
    
    def get_simplified_color(self, r, g, b):
        """将颜色简化为RGB格式"""
        if self.minimal_color:
            # 与富文本一致：取每个通道的高4位
            r_simple = (r // 16) * 16  # 例如：255 → 240 (0xF0), 123 → 112 (0x70)
            g_simple = (g // 16) * 16
            b_simple = (b // 16) * 16
            return (r_simple, g_simple, b_simple)
        return (r, g, b)
    
    def get_original_color_tag(self, r, g, b, a=None):
        """生成原始颜色标签"""
        # 确保a是整数
        a_val = a if a is not None else 255
        # 判断是否为透明像素（基于半透明阈值）
        is_transparent = self.has_alpha and a_val < self.alpha_threshold
        
        # 透明字符替代模式下，小于阈值的像素强制使用#0000
        if self.trans_opt == 1 and is_transparent:
            return '<color=#0000>'
        
        # 判断是否完全不透明（有A通道且A=255）
        is_fully_opaque = a_val == 255
        # 判断是否完全透明（A=0）
        is_fully_transparent = self.has_alpha and a_val == 0
        
        # 1. 完全透明像素（A=0）：无论开关状态，始终保留AA通道
        # 2. 半透明像素（0 < A < 255）：根据开关状态决定是否保留AA通道
        if is_fully_transparent:
            # 完全透明像素：保留AA通道
            if self.minimal_color:
                return f'<color=#{(r//16):01x}{(g//16):01x}{(b//16):01x}0>'  # #RGBA格式（A=0）
            else:
                return f'<color=#{r:02x}{g:02x}{b:02x}00>'  # #RRGGBBAA格式（A=00）
        elif 0 < a_val < 255:
            # 半透明像素：根据开关状态处理
            if self.keep_above_alpha:
                # 开关开启：保留AA通道
                if self.minimal_color:
                    return f'<color=#{(r//16):01x}{(g//16):01x}{(b//16):01x}{(a_val//16):01x}>'
                else:
                    return f'<color=#{r:02x}{g:02x}{b:02x}{a_val:02x}>'
            else:
                # 开关关闭：丢弃AA通道（强制不透明）
                if self.minimal_color:
                    return f'<color=#{(r//16):01x}{(g//16):01x}{(b//16):01x}>'
                else:
                    return f'<color=#{r:02x}{g:02x}{b:02x}>'
        else:
            # 完全不透明像素：正常处理
            if self.minimal_color:
                return f'<color=#{(r//16):01x}{(g//16):01x}{(b//16):01x}>'
            else:
                return f'<color=#{r:02x}{g:02x}{b:02x}>'

    def get_custom_color_tag(self):
        """生成自定义背景色的颜色标签"""
        if self.minimal_color:
            try:
                if len(self.custom_color) == 7:  # #RRGGBB格式
                    # 解析RR、GG、BB的十六进制值
                    r_hex = self.custom_color[1:3]
                    g_hex = self.custom_color[3:5]
                    b_hex = self.custom_color[5:7]
                    
                    # 转换为十进制
                    r = int(r_hex, 16)
                    g = int(g_hex, 16)
                    b = int(b_hex, 16)
                    
                    # 强制转为1位十六进制
                    r_short = r // 16
                    g_short = g // 16
                    b_short = b // 16
                    return f'<color=#{r_short:01x}{g_short:01x}{b_short:01x}>'
                elif len(self.custom_color) == 4:  # 已经是#RGB格式
                    return f'<color={self.custom_color}>'
                else:
                    # 其他格式保留原样
                    return f'<color={self.custom_color}>'
            except:
                # 解析失败时使用原始颜色
                return f'<color={self.custom_color}>'
        
        return f'<color={self.custom_color}>'

    def process_char_segment_line_safe(self):
        """字符分段处理"""
        segment_parts = [self.font_start]
        current_char_count = self.font_tag_len
        current_color_tag = None
        current_color_pixel_count = 0
        current_color_rgba = None  # 存储当前颜色的RGBA值用于相似度比较
        pixel_count_in_seg = 0
        is_line_end = False
        
        # 处理当前位置开始的像素，直到：1）超字符限制；2）行结束
        while self.current_x < self.img_w:
            # 获取当前像素
            if self.has_alpha:
                r, g, b, a = self.pixels[self.current_x, self.current_y]
                current_rgba = (r, g, b, a)
            else:
                r, g, b = self.pixels[self.current_x, self.current_y]
                a = 255
                current_rgba = (r, g, b)
            
            # 判断是否为透明像素（基于半透明阈值）
            is_transparent = self.has_alpha and a < self.alpha_threshold
            
            # 处理透明像素方案
            if is_transparent:
                if self.trans_opt == 2:  # 全角空格替代
                    color_tag = None  # 空格不需要颜色标签
                elif self.trans_opt == 1:  # 保持透明（根据极简模式选择格式）
                    color_tag = self.get_original_color_tag(r, g, b, a)
                else:  # 自定义背景色
                    color_tag = self.get_custom_color_tag()
            else:
                # 非透明像素：调用统一颜色标签生成方法
                color_tag = self.get_original_color_tag(r, g, b, a)
            
            # 计算添加当前像素后的字符长度
            if color_tag == current_color_tag or (
                current_color_tag is not None and 
                self.are_colors_similar(current_color_rgba, current_rgba)
            ):
                # 同色或相似色：空格替代时按空格数计算字符长度，否则按█计算
                if is_transparent and self.trans_opt == 2:
                    add_char_count = self.space_count
                else:
                    add_char_count = 1
                new_char_count = current_char_count + add_char_count
            else:
                # 不同色：计算颜色标签+内容的总长度
                if is_transparent and self.trans_opt == 2:
                    # 空格不需要颜色标签，直接加空格数
                    add_char_count = self.space_count
                    new_char_count = current_char_count + add_char_count
                else:
                    # 普通颜色标签：标签长度 + 内容长度 + 闭合标签长度
                    add_char_count = len(color_tag) + 1 + len('</color>') if color_tag else 1
                    new_char_count = current_char_count + add_char_count
            
            # 若超字符限制，停止当前段（当前像素不加入，留到下一段）
            if new_char_count > self.char_limit:
                # 若当前段为空（第一个像素就超限制），强制加入（避免空段）
                if pixel_count_in_seg == 0:
                    if is_transparent and self.trans_opt == 2:
                        segment_parts.append('　' * self.space_count)
                    else:
                        segment_parts.append(f"{color_tag}█</color>" if color_tag else '█')
                    segment_parts.append(self.font_end)
                    pixel_count_in_seg = 1
                    self.update_preview(self.current_x, self.current_y, r, g, b, a, current_color_rgba)
                    self.current_x += 1
                break
            
            # 同色或相似色合并处理
            if color_tag == current_color_tag or (
                current_color_tag is not None and 
                self.are_colors_similar(current_color_rgba, current_rgba)
            ):
                current_color_pixel_count += 1
            else:
                # 先添加之前的颜色块或空格
                if current_color_tag is not None:
                    segment_parts.append(f"{current_color_tag}{'█' * current_color_pixel_count}</color>")
                elif current_color_tag is None and self.trans_opt == 2 and pixel_count_in_seg > 0:
                    segment_parts.append('　' * (current_color_pixel_count * self.space_count))
                
                current_color_tag = color_tag
                current_color_rgba = current_rgba if color_tag else None
                current_color_pixel_count = 1
            
            # 更新状态
            current_char_count = new_char_count
            pixel_count_in_seg += 1
            self.update_preview(self.current_x, self.current_y, r, g, b, a, current_color_rgba)
            self.current_x += 1
        
        # 添加最后一个颜色块或空格
        if current_color_tag is not None:
            segment_parts.append(f"{current_color_tag}{'█' * current_color_pixel_count}</color>")
        elif current_color_tag is None and self.trans_opt == 2 and pixel_count_in_seg > 0:
            segment_parts.append('　' * (current_color_pixel_count * self.space_count))
        
        segment_parts.append(self.font_end)
        
        # 行结束判断
        is_line_end = (self.current_x >= self.img_w)
        
        return ''.join(segment_parts), is_line_end, pixel_count_in_seg

    def process_pixel_segment(self):
        """按像素分段"""
        segment_parts = [self.font_start]
        current_pixel_count = 0
        is_line_end = False
        current_color_tag = None
        color_pixel_count = 0
        current_color_rgba = None  # 存储当前颜色的RGBA值用于相似度比较
        
        while self.current_y < self.img_h and current_pixel_count < self.pixel_limit:
            if self.current_x >= self.img_w:
                is_line_end = True
                break
            
            # 获取像素
            if self.has_alpha:
                r, g, b, a = self.pixels[self.current_x, self.current_y]
                current_rgba = (r, g, b, a)
            else:
                r, g, b = self.pixels[self.current_x, self.current_y]
                a = 255
                current_rgba = (r, g, b)
            
            # 判断是否为透明像素（基于半透明阈值）
            is_transparent = self.has_alpha and a < self.alpha_threshold
            
            # 处理透明像素方案
            if is_transparent:
                if self.trans_opt == 2:  # 全角空格替代
                    color_tag = None  # 空格不需要颜色标签
                elif self.trans_opt == 1:  # 保持透明（根据极简模式选择格式）
                    color_tag = self.get_original_color_tag(r, g, b, a)
                else:  # 自定义背景色
                    color_tag = self.get_custom_color_tag()
            else:
                # 非透明像素：调用统一颜色标签生成方法
                color_tag = self.get_original_color_tag(r, g, b, a)
            
            # 判断是否为相同颜色或相似颜色
            if color_tag == current_color_tag or (
                current_color_tag is not None and 
                self.are_colors_similar(current_color_rgba, current_rgba)
            ):
                color_pixel_count += 1
            else:
                # 先添加之前的颜色块或空格
                if current_color_tag is not None:
                    segment_parts.append(f"{current_color_tag}{'█' * color_pixel_count}</color>")
                elif current_color_tag is None and self.trans_opt == 2 and color_pixel_count > 0:
                    segment_parts.append('　' * (color_pixel_count * self.space_count))
                
                current_color_tag = color_tag
                current_color_rgba = current_rgba if color_tag else None
                color_pixel_count = 1
            
            # 更新预览图（应用相近颜色合并和极简色彩）
            self.update_preview(self.current_x, self.current_y, r, g, b, a, current_color_rgba)
            
            self.current_x += 1
            current_pixel_count += 1
            
            # 行尾判断
            if self.current_x >= self.img_w:
                is_line_end = True
                break
        
        # 添加最后一个颜色块或空格
        if current_color_tag is not None:
            segment_parts.append(f"{current_color_tag}{'█' * color_pixel_count}</color>")
        elif current_color_tag is None and self.trans_opt == 2 and color_pixel_count > 0:
            segment_parts.append('　' * (color_pixel_count * self.space_count))
        
        segment_parts.append(self.font_end)
        
        return ''.join(segment_parts), is_line_end, current_pixel_count
    
    def update_preview(self, x, y, r, g, b, a=255, current_color_rgba=None):
        """更新预览图像素"""
        # 判断是否为透明像素（基于半透明阈值）
        is_transparent = self.has_alpha and a < self.alpha_threshold
        # 判断是否完全透明（A=0）
        is_fully_transparent = self.has_alpha and a == 0
        
        # 完全透明像素（A=0）始终保持透明，不受开关影响
        if is_fully_transparent:
            self.preview.setPixelColor(x, y, QColor(r, g, b, 0))
        else:
            # 非完全透明像素：按原有逻辑处理
            if not self.keep_above_alpha and self.has_alpha and a > self.alpha_threshold:
                a = 255
            
            if is_transparent:
                if self.trans_opt == 1:  # 透明字符替代：强制使用#0000对应的透明色
                    self.preview.setPixelColor(x, y, QColor(0, 0, 0, 0))
                elif self.trans_opt == 0:  # 自定义背景色（应用极简色彩，但保持纯色）
                    if self.minimal_color and len(self.custom_color) == 7:
                        try:
                            cr = int(self.custom_color[1:3], 16) // 16 * 16
                            cg = int(self.custom_color[3:5], 16) // 16 * 16
                            cb = int(self.custom_color[5:7], 16) // 16 * 16
                            self.preview.setPixelColor(x, y, QColor(cr, cg, cb, 255))
                        except:
                            self.preview.setPixelColor(x, y, QColor(self.custom_color))
                    else:
                        self.preview.setPixelColor(x, y, QColor(self.custom_color))
                elif self.trans_opt == 2:  # 全角空格替代（透明块占位）
                    self.preview.setPixelColor(x, y, QColor(0, 0, 0, 0))
            else:
                # 确保每个像素是纯净的单色，无混合
                if self.merge_similar and current_color_rgba is not None:
                    # 相近颜色合并：使用当前颜色块的纯色
                    sr, sg, sb = self.get_simplified_color(current_color_rgba[0], current_color_rgba[1], current_color_rgba[2])
                    sa = current_color_rgba[3] if len(current_color_rgba) == 4 else 255
                    # 应用半透明丢弃逻辑
                    if not self.keep_above_alpha and self.has_alpha and sa > self.alpha_threshold:
                        sa = 255
                    self.preview.setPixelColor(x, y, QColor(sr, sg, sb, sa))
                else:
                    # 仅应用极简色彩，保持纯色
                    sr, sg, sb = self.get_simplified_color(r, g, b)
                    self.preview.setPixelColor(x, y, QColor(sr, sg, sb, a))

class PreviewDialog(QDialog):
    def __init__(self, preview_img, target_w, target_h, orig_w, orig_h, parent=None):
        super().__init__(parent)
        self.setWindowTitle("预览效果")
        self.resize(800, 700)
        self.preview_img = preview_img  # 原始预览图
        self.target_w = target_w
        self.target_h = target_h
        
        layout = QVBoxLayout(self)
        info = QLabel(f"目标分辨率: {target_w}x{target_h} | 原图分辨率: {orig_w}x{orig_h}")
        info.setStyleSheet("color: #00c8ff; font-weight: bold;")
        layout.addWidget(info)
        
        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.scroll_container = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_container)
        self.scroll_layout.setAlignment(Qt.AlignCenter)
        scroll.setWidget(self.scroll_container)
        layout.addWidget(scroll, 1)
        
        # 缩放控制
        scale_layout = QHBoxLayout()
        scale_layout.addWidget(QLabel("缩放比例:"))
        self.scale_slider = QSpinBox()
        self.scale_slider.setRange(1, 30)
        self.scale_slider.setSuffix("x")
        
        # 计算默认缩放比例
        default_scale = min(800/target_w, 600/target_h, 8.0)
        self.scale_slider.setValue(int(default_scale))
        
        self.scale_slider.valueChanged.connect(self.scale_preview)
        scale_layout.addWidget(self.scale_slider)
        scale_layout.addStretch()
        layout.addLayout(scale_layout)
        
        # 初始化图片显示控件
        self.img_label = QLabel()
        self.img_label.setAlignment(Qt.AlignCenter)
        self.scroll_layout.addWidget(self.img_label)
        
        # 初始缩放
        self.scale_preview(int(default_scale))
        
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok)
        btn_box.accepted.connect(self.accept)
        layout.addWidget(btn_box)
    
    def scale_preview(self, scale):
        """缩放预览图"""
        if scale <= 0:
            return
        
        # 计算缩放后的尺寸
        sw = int(self.target_w * scale)
        sh = int(self.target_h * scale)
        
        # 使用FastTransformation保持像素锐利
        scaled_img = self.preview_img.scaled(
            sw, sh, 
            Qt.KeepAspectRatio, 
            Qt.FastTransformation
        )
        
        # 更新图片
        self.img_label.setPixmap(QPixmap.fromImage(scaled_img))

class ImageToRichTextApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("图片转富文本工具 By Hfohn")
        self.setGeometry(100, 100, 850, 950)
        self.setMinimumSize(600, 700)
        
        # 打包时设置图标
        self.setWindowIcon(QIcon(os.path.join(sys._MEIPASS, "千星图标.png")) if hasattr(sys, '_MEIPASS') else QIcon("千星图标.png"))
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
        self.total_segments = 0
        self.original_width = 0
        self.original_height = 0
        self.target_width = 40
        self.target_height = 30
        self.minimal_color = False  # 极简色彩开关
        self.merge_similar = False  # 相近颜色合并开关
        self.similarity_threshold = 10  # 颜色相似度阈值
        self.alpha_threshold = 128  # 半透明阈值
        self.keep_above_alpha = True  # 保留高于阈值半透明（默认开启）
        self.space_replacement_enabled = False  # 是否启用全角空格替代
        self.space_count = 1  # 每个像素的全角空格数
        self.segment_labels = []  # 存储分段标签
    
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
            QLabel#segmentLabel { color: #ffff66; font-weight: bold; font-size: 14px; margin-right: 10px; }
            QSlider::groove:horizontal {
                border: 1px solid #515151;
                height: 8px;
                background: #3f3f46;
                margin: 2px 0;
                border-radius: 4px;
            }
            QSlider::handle:horizontal {
                background: #0078d4;
                border: 1px solid #5c5c5c;
                width: 18px;
                margin: -5px 0;
                border-radius: 8px;
            }
            QLabel#hintLabel { color: #888888; font-size: 11px; margin-top: 3px; }
            QSpinBox#pageSpin {
                width: 50px;
                text-align: center;
                background-color: #3f3f46;
                border: 1px solid #515151;
                padding: 2px;
            }
            QLabel#pageLabelText { color: #e0e0e0; font-size: 14px; }
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
        title_label.setStyleSheet("font-size: 24px; font-weight: bold; color: #339af0; margin-bottom: 15px;")
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
        
        # 保持宽高比
        self.keep_aspect_checkbox = QCheckBox("保持宽高比")
        self.keep_aspect_checkbox.setChecked(True)
        resolution_layout.addWidget(self.keep_aspect_checkbox)
        
        # 目标分辨率输入
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
        
        self.target_width_spin.valueChanged.connect(self.on_resolution_changed)
        self.target_height_spin.valueChanged.connect(self.on_resolution_changed)
        
        resolution_layout.addLayout(target_layout)
        resolution_group.setLayout(resolution_layout)
        scroll_layout.addWidget(resolution_group)
        
        # 3. 半透明像素处理
        transparent_group = QGroupBox("3. 半透明像素处理")
        transparent_layout = QVBoxLayout()
        self.transparent_bg = QButtonGroup(self)
        
        # 半透明阈值设置
        alpha_layout = QHBoxLayout()
        alpha_layout.addWidget(QLabel("完全透明阈值（低于该值视为完全透明）:"))
        self.alpha_slider = QSlider(Qt.Horizontal)
        self.alpha_slider.setRange(0, 255)
        self.alpha_slider.setValue(self.alpha_threshold)
        self.alpha_slider.setTickInterval(20)
        self.alpha_slider.setTickPosition(QSlider.TicksBelow)
        self.alpha_value_label = QLabel(f"{self.alpha_threshold}")
        self.alpha_slider.valueChanged.connect(self.on_alpha_changed)
        
        # 保留高于阈值半透明的开关（默认开启）
        self.keep_above_alpha_checkbox = QCheckBox("保留高于此阈值半透明")
        self.keep_above_alpha_checkbox.setChecked(self.keep_above_alpha)
        self.keep_above_alpha_checkbox.stateChanged.connect(self.on_keep_above_alpha_changed)
        
        # 布局调整：滑块+数值+开关
        alpha_layout.addWidget(self.alpha_slider)
        alpha_layout.addWidget(self.alpha_value_label)
        alpha_layout.addSpacing(20)  # 增加间距
        alpha_layout.addWidget(self.keep_above_alpha_checkbox)
        alpha_layout.addStretch()
        transparent_layout.addLayout(alpha_layout)
        
        # 处理方案
        transparent_layout.addWidget(QLabel("完全透明像素处理方案(背景色):"))
        
        # 保持透明选项（默认勾选）
        self.keep_transparent_radio = QRadioButton("透明字符替代(富文本)")
        self.keep_transparent_radio.setChecked(True)
        transparent_layout.addWidget(self.keep_transparent_radio)
        
        # 自定义背景色
        color_btn_layout = QHBoxLayout()
        self.custom_color_radio = QRadioButton("自定义颜色替代(富文本):")
        color_btn_layout.addWidget(self.custom_color_radio)
        self.color_button = QPushButton(self.custom_color)
        self.color_button.setFixedWidth(80)
        self.color_button.clicked.connect(self.choose_color)
        color_btn_layout.addWidget(self.color_button)
        color_btn_layout.addStretch()
        transparent_layout.addLayout(color_btn_layout)
        
        # 全角空格替代
        space_layout = QHBoxLayout()
        space_layout.setAlignment(Qt.AlignLeft)
        self.space_radio = QRadioButton("全角空格替代")
        space_layout.addWidget(self.space_radio)
        space_layout.addWidget(QLabel("每个像素使用全角空格数:"))
        self.space_count_spin = QSpinBox()
        self.space_count_spin.setRange(1, 10)
        self.space_count_spin.setValue(self.space_count)
        self.space_count_spin.setFixedWidth(60)
        space_layout.addWidget(self.space_count_spin)
        space_layout.addStretch()
        transparent_layout.addLayout(space_layout)
        
        # 按钮组（0=自定义颜色替代，1=透明字符替代，2=全角空格替代）
        self.transparent_bg.addButton(self.keep_transparent_radio, 1)
        self.transparent_bg.addButton(self.custom_color_radio, 0)
        self.transparent_bg.addButton(self.space_radio, 2)
        
        transparent_group.setLayout(transparent_layout)
        scroll_layout.addWidget(transparent_group)
        
        # 4. 颜色优化设置
        color_optimization_group = QGroupBox("4. 颜色优化设置")
        color_optimization_layout = QVBoxLayout()
        
        # 极简色彩开关
        minimal_color_layout = QHBoxLayout()
        self.minimal_color_checkbox = QCheckBox(
            "启用极简色彩（#RRGGBB→#RGB，#RRGGBBAA→#RGBA，牺牲精度）"
        )
        self.minimal_color_checkbox.setChecked(self.minimal_color)
        self.minimal_color_checkbox.toggled.connect(self.on_minimal_color_changed)
        minimal_color_layout.addWidget(self.minimal_color_checkbox)
        minimal_color_layout.addStretch()
        color_optimization_layout.addLayout(minimal_color_layout)
        
        # 相近颜色合并
        merge_layout = QHBoxLayout()
        merge_layout.setAlignment(Qt.AlignLeft)
        merge_layout.setSpacing(10)
        self.merge_similar_checkbox = QCheckBox("启用相近颜色合并")
        self.merge_similar_checkbox.setChecked(self.merge_similar)
        self.merge_similar_checkbox.toggled.connect(self.on_merge_similar_changed)
        merge_layout.addWidget(self.merge_similar_checkbox)
        
        merge_layout.addWidget(QLabel("相似度阈值:"))
        self.similarity_slider = QSlider(Qt.Horizontal)
        self.similarity_slider.setRange(0, 255)
        self.similarity_slider.setValue(self.similarity_threshold)
        self.similarity_slider.setTickInterval(10) # 默认值10
        self.similarity_slider.setTickPosition(QSlider.TicksBelow)
        self.similarity_slider.setEnabled(self.merge_similar)
        self.similarity_slider.setFixedWidth(200)
        # 绑定valueChanged信号
        self.similarity_slider.valueChanged.connect(self.on_similarity_changed)
        self.similarity_value_label = QLabel(f"{self.similarity_threshold}")
        merge_layout.addWidget(self.similarity_slider)
        merge_layout.addWidget(self.similarity_value_label)
        merge_layout.addStretch()
        color_optimization_layout.addLayout(merge_layout)
        
        color_optimization_group.setLayout(color_optimization_layout)
        scroll_layout.addWidget(color_optimization_group)
        
        # 5. 像素点大小设置
        font_group = QGroupBox("5. 像素点大小设置")
        font_layout = QHBoxLayout()
        self.use_font_size_checkbox = QCheckBox("设置像素点大小:")
        self.use_font_size_checkbox.setChecked(True)
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(1, 100)
        self.font_size_spin.setValue(5)
        self.font_size_spin.setEnabled(True)
        self.font_size_spin.setFixedWidth(60)
        self.use_font_size_checkbox.toggled.connect(self.font_size_spin.setEnabled)
        font_layout.addWidget(self.use_font_size_checkbox)
        font_layout.addWidget(self.font_size_spin)
        font_layout.addStretch()
        font_group.setLayout(font_layout)
        scroll_layout.addWidget(font_group)
        
        # 6. 分段规则
        segment_group = QGroupBox("6. 分段规则")
        segment_layout = QVBoxLayout()
        self.segment_rule_group = QButtonGroup(self)
        self.segment_rule_group.setExclusive(True)
        
        # 按字符分段
        char_layout = QHBoxLayout()
        self.char_segment_radio = QRadioButton("按字符长度分段（每段最多:）")
        self.char_segment_radio.setChecked(True)  # 默认勾选
        self.char_segment_spin = QSpinBox()
        self.char_segment_spin.setRange(1, 99999)
        self.char_segment_spin.setValue(1000)  # 字符默认1000
        self.char_segment_spin.setFixedWidth(80)
        char_layout.addWidget(self.char_segment_radio)
        char_layout.addWidget(self.char_segment_spin)
        char_layout.addStretch()
        segment_layout.addLayout(char_layout)
        
        # 按像素分段
        pixel_layout = QHBoxLayout()
        self.pixel_segment_radio = QRadioButton("按像素数量分段（每段最多:）")
        self.pixel_segment_spin = QSpinBox()
        self.pixel_segment_spin.setRange(1, 99999)
        self.pixel_segment_spin.setValue(40)  # 像素默认40
        self.pixel_segment_spin.setFixedWidth(80)
        pixel_layout.addWidget(self.pixel_segment_radio)
        pixel_layout.addWidget(self.pixel_segment_spin)
        pixel_layout.addStretch()
        segment_layout.addLayout(pixel_layout)
        
        self.segment_rule_group.addButton(self.pixel_segment_radio, 0)
        self.segment_rule_group.addButton(self.char_segment_radio, 1)
        segment_group.setLayout(segment_layout)
        scroll_layout.addWidget(segment_group)
        
        # 7. 操作按钮
        button_group = QGroupBox("7. 操作")
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
        
        # 8. 结果展示
        result_group = QGroupBox("8. 处理结果")
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
        nav_layout.setContentsMargins(5, 5, 5, 5)
        nav_layout.setSpacing(15)
        
        self.prev_btn = QPushButton("上一页")
        self.prev_btn.setObjectName("navBtn")
        self.prev_btn.setEnabled(False)
        self.prev_btn.clicked.connect(self.prev_page)
        
        # 页码显示区域（居中布局）
        page_display_layout = QHBoxLayout()
        page_display_layout.setSpacing(8)  # 文字与输入框间距
        
        # 共X段
        total_segments_label = QLabel("共 0 段，")
        total_segments_label.setObjectName("pageLabelText")
        page_display_layout.addWidget(total_segments_label)
        
        # 第X页
        label1 = QLabel("第")
        label1.setObjectName("pageLabelText")
        page_display_layout.addWidget(label1)
        
        self.page_spin = QSpinBox()
        self.page_spin.setObjectName("pageSpin")
        self.page_spin.setRange(1, 999)  # 支持三位数页码
        self.page_spin.setValue(1)
        self.page_spin.valueChanged.connect(self.on_page_spin_changed)
        page_display_layout.addWidget(self.page_spin)
        
        # 共X页
        self.total_pages_label = QLabel("页，共 0 页")
        self.total_pages_label.setObjectName("pageLabelText")
        page_display_layout.addWidget(self.total_pages_label)
        
        # 页码区域居中
        page_container = QWidget()
        page_container.setLayout(page_display_layout)
        
        self.next_btn = QPushButton("下一页")
        self.next_btn.setObjectName("navBtn")
        self.next_btn.setEnabled(False)
        self.next_btn.clicked.connect(self.next_page)
        
        # 导航布局：左右按钮 + 中间居中的页码区域
        nav_layout.addWidget(self.prev_btn)
        nav_layout.addWidget(page_container, 1, Qt.AlignCenter)  # 中间区域拉伸并居中
        nav_layout.addWidget(self.next_btn)
        
        # 保存总段数标签引用，方便后续更新
        self.total_segments_display_label = total_segments_label
        
        result_layout.addWidget(nav_frame)
        result_group.setLayout(result_layout)
        main_layout.addWidget(result_group)
    
    # 开关状态变化处理
    def on_keep_above_alpha_changed(self, state):
        """保留高于阈值半透明开关状态变化"""
        self.keep_above_alpha = (state == Qt.Checked)
    
    def on_page_spin_changed(self, value):
        """页码输入框变化时跳转页面"""
        if self.total_pages == 0:
            return
        # 转换为0-based索引
        target_page = value - 1
        if 0 <= target_page < self.total_pages and target_page != self.current_page:
            self.current_page = target_page
            self.display_page(self.current_page)
    
    def update_page_navigation(self):
        """更新分页导航控件状态"""
        if self.total_pages == 0:
            self.page_spin.setRange(1, 999)
            self.page_spin.setValue(1)
            self.total_segments_display_label.setText("共 0 段，")
            self.total_pages_label.setText("页，共 0 页")
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
        else:
            # 更新总段数显示
            self.total_segments_display_label.setText(f"共 {self.total_segments} 段，")
            # 页码范围限制在1-总页数（最多三位数）
            max_page = min(self.total_pages, 999)
            self.page_spin.setRange(1, max_page)
            self.page_spin.setValue(self.current_page + 1)  # 转换为1-based
            self.total_pages_label.setText(f"页，共 {self.total_pages} 页")
            self.prev_btn.setEnabled(self.current_page > 0)
            self.next_btn.setEnabled(self.current_page < self.total_pages - 1)
    
    def on_alpha_changed(self, value):
        """半透明阈值变化"""
        self.alpha_threshold = value
        self.alpha_value_label.setText(f"{value}")
    
    def on_minimal_color_changed(self, checked):
        """极简色彩开关变化"""
        self.minimal_color = checked
    
    def on_merge_similar_changed(self, checked):
        """相近颜色合并开关变化"""
        self.merge_similar = checked
        self.similarity_slider.setEnabled(checked)
    
    def on_similarity_changed(self, value):
        """相似度阈值变化"""
        self.similarity_threshold = value
        self.similarity_value_label.setText(f"{value}")  # 实时更新显示数值
    
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
        
        # 获取透明处理选项
        transparent_option = self.transparent_bg.checkedId()
        # 获取分段规则（1=按字符分段，0=按像素分段）
        segment_rule = 1 if self.char_segment_radio.isChecked() else 0
        pixel_segment_size = self.pixel_segment_spin.value()
        char_segment_size = self.char_segment_spin.value()
        font_size = self.font_size_spin.value() if self.use_font_size_checkbox.isChecked() else 0
        # 获取空格替代相关设置
        space_replacement_enabled = (transparent_option == 2)
        space_count = self.space_count_spin.value()
        
        # 清空结果
        self.clear_results()
        
        # 创建线程
        self.thread = ImageProcessorThread(
            image_path, self.target_width, self.target_height, transparent_option,
            self.custom_color, font_size, segment_rule, pixel_segment_size, char_segment_size,
            self.minimal_color, self.merge_similar, self.similarity_threshold,
            self.alpha_threshold, self.keep_above_alpha,
            space_replacement_enabled, space_count
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
    
    def on_finish(self, segments, line_end_markers, segment_line_mapping, total_pixel_count, preview_image, width, height, original_width, original_height, segment_labels):
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
        self.segment_labels = segment_labels  # 保存分段标签
        self.total_segments = len(segments)  # 更新总段数
        
        # 分页计算
        self.total_pages = (len(self.results) + self.items_per_page - 1) // self.items_per_page
        self.current_page = 0
        
        # 更新分页导航
        self.update_page_navigation()
        
        # 更新界面
        self.preview_btn.setEnabled(True)
        self.display_page(0)
        
        # 恢复按钮
        self.process_btn.setEnabled(True)
        rule_name = "按字符分段" if self.char_segment_radio.isChecked() else "按像素分段"
        QMessageBox.information(self, "处理完成", f"共生成 {len(self.results)} 段富文本（{rule_name}），总计 {total_pixel_count} 个像素（应等于 {width * height}）")
    
    def display_page(self, page_number):
        """显示当前页结果"""
        self.clear_results()
        if not self.results:
            return
        
        start_idx = page_number * self.items_per_page
        end_idx = min(start_idx + self.items_per_page, len(self.results))
        
        for i in range(start_idx, end_idx):
            segment = self.results[i]
            is_line_end = i in self.line_end_markers
            segment_label = self.segment_labels[i] if i < len(self.segment_labels) else f"第{self.segment_line_mapping[i]}行第{i - start_idx + 1}段"
            
            # 统计信息（包含透明像素的█占位）
            clean_text = re.sub(r'<[^>]+>', '', segment)
            pixel_count = clean_text.count('█') + clean_text.count('　')  # 包含全角空格和透明占位的█
            char_count = len(segment)
            
            # 段落容器
            segment_frame = QFrame()
            segment_frame.setObjectName("segmentFrame")
            segment_layout = QVBoxLayout(segment_frame)
            
            # 头部（分段标签 + 行尾标记 + 统计信息）
            header_layout = QHBoxLayout()
            # 添加分段标签（黄色粗体）
            label_widget = QLabel(segment_label)
            label_widget.setObjectName("segmentLabel")
            header_layout.addWidget(label_widget)
            # 行尾标记
            if is_line_end:
                end_marker = QLabel("【行尾】")
                end_marker.setObjectName("lineEndMarker")
                header_layout.addWidget(end_marker)
            # 统计信息
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
            
            # 复制区域
            copy_layout = QHBoxLayout()
            copy_layout.addStretch()
            
            # 已复制标签（默认隐藏，绿色粗体）
            copied_label = QLabel("已复制")
            copied_label.setStyleSheet("color: #00ff9d; font-weight: bold; margin-right: 8px;")
            copied_label.setVisible(False)
            copy_layout.addWidget(copied_label)
            
            # 复制按钮
            copy_btn = QPushButton("复制")
            copy_btn.setObjectName("copyBtn")
            # 绑定事件时传递标签
            copy_btn.clicked.connect(lambda _, t=segment, lbl=copied_label: self.copy_segment(t, lbl))
            copy_layout.addWidget(copy_btn)
            
            segment_layout.addLayout(copy_layout)
            
            self.scroll_layout.addWidget(segment_frame)
        
        self.scroll_layout.addStretch()
        # 更新分页导航状态
        self.update_page_navigation()
    
    def clear_results(self):
        """清空结果区域"""
        while self.scroll_layout.count() > 0:
            item = self.scroll_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
    
    def copy_segment(self, text, copied_label):
        """复制段落（显示常驻已复制标签）"""
        pyperclip.copy(text)
        copied_label.setVisible(True)
    
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
    window.init_style()  # 初始化样式
    window.init_ui()     # 初始化界面
    window.show()
    sys.exit(app.exec_())