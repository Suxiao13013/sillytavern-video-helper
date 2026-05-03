"""
视频拼接工具
使用FFmpeg进行视频后处理：拼接、添加音频、转码
"""

import os
import subprocess
import json
from typing import List, Optional, Dict


class VideoConcat:
    """视频拼接与后处理"""
    
    def __init__(self, ffmpeg_path: str = "ffmpeg", ffprobe_path: str = "ffprobe"):
        self.ffmpeg = ffmpeg_path
        self.ffprobe = ffprobe_path
    
    def get_video_info(self, video_path: str) -> Dict:
        """获取视频信息"""
        try:
            result = subprocess.run([
                self.ffprobe, "-v", "quiet",
                "-print_format", "json",
                "-show_format", "-show_streams",
                video_path
            ], capture_output=True, text=True)
            return json.loads(result.stdout)
        except Exception as e:
            return {"error": str(e)}
    
    def concat_with_transition(self, video_paths: List[str], 
                              output_path: str,
                              transition: str = "fade",
                              transition_duration: float = 0.5) -> bool:
        """
        带转场效果的视频拼接
        
        transition: fade, dissolve, wipeleft, wiperight
        """
        if len(video_paths) < 2:
            if video_paths:
                import shutil
                shutil.copy2(video_paths[0], output_path)
                return True
            return False
        
        # 获取每个视频的时长
        durations = []
        for vp in video_paths:
            info = self.get_video_info(vp)
            try:
                duration = float(info["format"]["duration"])
                durations.append(duration)
            except (KeyError, ValueError):
                durations.append(5.0)  # 默认5秒
        
        # 构建FFmpeg滤镜
        inputs = []
        filter_parts = []
        
        for i, vp in enumerate(video_paths):
            inputs.extend(["-i", vp])
        
        if transition == "fade":
            # 使用xfade滤镜实现淡入淡出
            filter_str = ""
            current = "[0:v]"
            
            for i in range(1, len(video_paths)):
                offset = sum(durations[:i]) - transition_duration * i
                offset = max(0, offset)
                
                if i < len(video_paths) - 1:
                    next_label = f"[v{i}]"
                else:
                    next_label = "[outv]"
                
                filter_str += (
                    f"{current}[{i}:v]xfade=transition=fade:"
                    f"duration={transition_duration}:offset={offset:.2f}{next_label};"
                )
                current = next_label
            
            # 音频淡入淡出
            audio_str = ""
            a_current = "[0:a]"
            for i in range(1, len(video_paths)):
                if i < len(video_paths) - 1:
                    a_next = f"[a{i}]"
                else:
                    a_next = "[outa]"
                
                audio_str += (
                    f"{a_current}[{i}:a]acrossfade=d={transition_duration}{a_next};"
                )
                a_current = a_next
            
            full_filter = filter_str.rstrip(";")
            if audio_str:
                full_filter += ";" + audio_str.rstrip(";")
            
            cmd = [
                self.ffmpeg, *inputs,
                "-filter_complex", full_filter,
                "-map", "[outv]",
            ]
            if audio_str:
                cmd.extend(["-map", "[outa]"])
            
            cmd.extend([
                "-c:v", "libx264", "-crf", "18",
                "-preset", "medium",
                "-y", output_path
            ])
        else:
            # 简单拼接
            list_path = output_path + ".concat_list.txt"
            with open(list_path, "w") as f:
                for vp in video_paths:
                    f.write(f"file '{os.path.abspath(vp)}'\n")
            
            cmd = [
                self.ffmpeg, "-f", "concat", "-safe", "0",
                "-i", list_path,
                "-c:v", "libx264", "-crf", "18",
                "-y", output_path
            ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                print(f"FFmpeg错误: {result.stderr[-500:]}")
                # 回退到简单拼接
                return self.concat_simple(video_paths, output_path)
            return True
        except Exception as e:
            print(f"转场拼接失败: {e}")
            return self.concat_simple(video_paths, output_path)
    
    def concat_simple(self, video_paths: List[str], output_path: str) -> bool:
        """简单拼接（无转场）"""
        list_path = output_path + ".concat_list.txt"
        with open(list_path, "w") as f:
            for vp in video_paths:
                f.write(f"file '{os.path.abspath(vp)}'\n")
        
        try:
            subprocess.run([
                self.ffmpeg, "-f", "concat", "-safe", "0",
                "-i", list_path,
                "-c:v", "libx264", "-crf", "18",
                "-y", output_path
            ], capture_output=True, check=True, timeout=600)
            return True
        except Exception as e:
            print(f"简单拼接失败: {e}")
            return False
    
    def add_background_music(self, video_path: str, audio_path: str,
                            output_path: str, 
                            video_volume: float = 1.0,
                            audio_volume: float = 0.3) -> bool:
        """添加背景音乐"""
        try:
            subprocess.run([
                self.ffmpeg,
                "-i", video_path,
                "-i", audio_path,
                "-filter_complex",
                f"[0:a]volume={video_volume}[va];[1:a]volume={audio_volume}[ba];"
                f"[va][ba]amix=inputs=2:duration=first[aout]",
                "-map", "0:v", "-map", "[aout]",
                "-c:v", "copy",
                "-shortest",
                "-y", output_path
            ], capture_output=True, check=True, timeout=600)
            return True
        except Exception as e:
            print(f"添加音乐失败: {e}")
            return False
    
    def add_subtitles(self, video_path: str, srt_path: str,
                     output_path: str, 
                     font_size: int = 24,
                     font_color: str = "white") -> bool:
        """添加字幕"""
        try:
            subprocess.run([
                self.ffmpeg,
                "-i", video_path,
                "-vf", f"subtitles={srt_path}:force_style='FontSize={font_size},PrimaryColour=&H{font_color}'",
                "-c:v", "libx264", "-crf", "18",
                "-y", output_path
            ], capture_output=True, check=True, timeout=600)
            return True
        except Exception as e:
            print(f"添加字幕失败: {e}")
            return False
    
    def generate_srt(self, scenes: List[Dict], output_path: str):
        """从场景信息生成SRT字幕文件"""
        lines = []
        current_time = 0.0
        
        for i, scene in enumerate(scenes):
            duration = scene.get("duration", 5.0)
            title = scene.get("title", "")
            text = scene.get("subtitle", title)
            
            start = self._seconds_to_srt_time(current_time)
            end = self._seconds_to_srt_time(current_time + duration)
            
            lines.append(f"{i + 1}")
            lines.append(f"{start} --> {end}")
            lines.append(text)
            lines.append("")
            
            current_time += duration
        
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    
    def _seconds_to_srt_time(self, seconds: float) -> str:
        """秒数转SRT时间格式"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 3:
        print("用法: python video_concat.py <输出文件> <视频1> <视频2> ...")
        sys.exit(1)
    
    concat = VideoConcat()
    output = sys.argv[1]
    videos = sys.argv[2:]
    
    success = concat.concat_with_transition(videos, output)
    print(f"拼接{'成功' if success else '失败'}: {output}")
