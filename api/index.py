from flask import Flask, jsonify, request, Response
from io import BytesIO
from PIL import Image
import logging
import requests
import re
from py_mini_racer import MiniRacer
import json
import base64
import logging
from typing import Dict, List, Optional
import sys
import time
from urllib.parse import unquote, quote, urlencode

sys.stdout.reconfigure(encoding="utf-8")

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
app.debug = False
app.json.ensure_ascii = False


def decode_search_value(value: str) -> str:
    """
    判断并解码搜索值
    如果值是URL编码，则解码为中文，否则直接返回
    """
    # URL编码的特征：包含%后跟两个十六进制字符
    url_encoded_pattern = r"%[0-9A-Fa-f]{2}"

    # 如果包含URL编码特征，尝试解码
    if re.search(url_encoded_pattern, value):
        try:
            decoded = unquote(value)
            # 解码后如果还包含URL编码特征，说明可能有多重编码，继续解码
            while re.search(url_encoded_pattern, decoded):
                temp = unquote(decoded)
                if temp == decoded:  # 如果没有变化，停止解码
                    break
                decoded = temp
            return decoded
        except Exception:
            # 如果解码失败，返回原值
            return value
    else:
        # 没有URL编码特征，直接返回
        return value


# 配置
class Config:
    HEADERS = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
        "accept-encoding": "gzip, deflate, br",
        "accept-language": "zh-CN,zh;q=0.9",
        "cache-control": "max-age=0",
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": "Windows",
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "same-origin",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/103.0.0.0 Safari/537.36",
    }


class ComicParser:
    """漫画解析器"""

    @staticmethod
    def decode_base64_custom(T: str) -> bytes:
        """自定义Base64解码"""
        keyStr = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
        result = []
        e = 0

        while e < len(T):
            b = keyStr.index(T[e])
            e += 1
            d = keyStr.index(T[e])
            e += 1
            f = keyStr.index(T[e])
            e += 1
            g = keyStr.index(T[e])
            e += 1

            b = b << 2 | d >> 4
            d = (d & 15) << 4 | f >> 2
            h = (f & 3) << 6 | g

            result.append(b)
            if f != 64:
                result.append(d)
            if g != 64:
                result.append(h)

        return bytes(result)

    @staticmethod
    def get_comic_info(comic_id: str) -> Dict:
        """获取漫画基本信息"""
        url = f"https://ac.qq.com/Comic/comicInfo/id/{comic_id}"
        resp = requests.get(url, headers=Config.HEADERS)

        if resp.status_code != 200:
            return {"error": f"请求失败，状态码: {resp.status_code}"}

        # 提取标题
        title_match = re.search("<title>(.*?)</title>", resp.text)
        title = title_match.group(1) if title_match else "未知标题"
        comic_title = title.split("-")[0].strip() if "-" in title else title

        # 提取封面图片

        cover_match = re.search(
            r'<div class="works-cover[^"]*">\s*<a[^>]*>\s*<img src="([^"]*)"[^>]*>',
            resp.text,
        )

        if cover_match:
            cover_url = cover_match.group(1)
            # 处理可能的相对路径
            if cover_url.startswith("//"):
                cover_url = "https:" + cover_url
            elif cover_url.startswith("/"):
                cover_url = "https://ac.qq.com" + cover_url
        else:
            cover_url = None

        # 提取人气信息 - 匹配你提供的HTML结构
        popularity_match = re.search(r"<span>人气：<em>(.*?)</em></span>", resp.text)
        popularity = popularity_match.group(1).strip() if popularity_match else "未知"

        # 提取评分信息 - 匹配你提供的HTML结构
        rating_match = re.search(
            r"评分：<strong[^>]*>(.*?)</strong>\s*\(<span>(\d+)</span>人评分\)",
            resp.text,
        )
        if rating_match:
            rating = {
                "score": rating_match.group(1).strip(),
                "rating_count": rating_match.group(2).strip(),
            }
        else:
            rating = {"score": "未知", "rating_count": "0"}

        # 提取章节列表 - 匹配你提供的HTML结构
        # 查找所有章节链接，格式如：3.梦中人
        chapter_pattern = r'<a(?!.*?开始阅读)[^>]*?title="([^"]+?)"[^>]*?href="(/ComicView/index/id/\d+/cid/\d+)"[^>]*?>([\s\S]*?)</a>'
        chapter_matches = re.findall(chapter_pattern, resp.text)

        chapters = []
        for match in chapter_matches:
            # match[0] 是title属性中的章节名，match[1] 是链接，match[2] 是标签内的章节名
            title_attr = match[0].strip()  # title属性中的标题，如"王牌御史：01，缘起"
            link_text = re.sub(
                r"\s+", " ", match[2]
            ).strip()  # 标签内的文本，清理多余空格，如"01，缘起"
            chapter_link = "https://ac.qq.com" + match[1]

            # 优先使用title属性中的标题，如果没有则使用链接文本
            chapter_name = title_attr.split("：")[1]

            # 提取章节序号 - 更灵活的匹配方式
            chapter_num = len(chapters) + 1  # 默认序号

            chapters.append(
                {"number": chapter_num, "link": chapter_link, "title": chapter_name}
            )

        seen_links = set()
        unique_chapters = []

        for chapter in chapters:
            # 提取链接中的cid作为唯一标识
            cid_match = re.search(r"/cid/(\d+)", chapter["link"])
            if cid_match:
                cid = cid_match.group(1)
                if cid not in seen_links:
                    seen_links.add(cid)
                    unique_chapters.append(chapter)
            else:
                # 如果没有cid，使用章节名去重
                if chapter["link"] not in seen_links:
                    seen_links.add(chapter["link"])
                    unique_chapters.append(chapter)

        chapters = unique_chapters

        # 按章节号排序
        chapters.sort(key=lambda x: x["number"])

        return {
            "comic_id": comic_id,
            "title": comic_title,
            "popularity": popularity,
            "rating": rating,
            "chapters": chapters,
            "cover_url": cover_url,  # 新增封面URL
            "total_chapters": len(chapters),
        }

    @staticmethod
    def get_chapter_images(chapter_url: str) -> Dict:
        """获取章节图片数据"""
        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            try:
                resp = requests.get(chapter_url, headers=Config.HEADERS)
                if resp.status_code != 200:
                    return {"error": f"章节请求失败，状态码: {resp.status_code}"}

                html = resp.text

                # 提取章节标题
                chapter_title_match = re.search(
                    r"<title>《[^》]*》(.*?)-.*?</title>", html
                )
                chapter_title = (
                    chapter_title_match.group(1).strip()
                    if chapter_title_match
                    else "未知章节"
                )

                # 提取加密数据
                data_match = re.findall("(?<=var DATA = ').*?(?=')", html)
                if not data_match:
                    return {"error": "未找到加密数据"}

                data = data_match[0]

                # 提取nonce
                nonce_matches = re.findall('window\[".+?(?<=;)', html)
                if len(nonce_matches) < 2:
                    return {"error": "未找到nonce数据"}

                nonce = "=".join(nonce_matches[1].split("=")[1:])[:-1]
                ctx = MiniRacer()
                nonce = ctx.eval(nonce)

                # 解密数据
                T = list(data)
                N = re.findall("\d+[a-zA-Z]+", nonce)
                jlen = len(N)

                while jlen:
                    jlen -= 1
                    jlocate = int(re.findall("\d+", N[jlen])[0]) & 255
                    jstr = re.sub("\d+", "", N[jlen])
                    del T[jlocate : jlocate + len(jstr)]

                T = "".join(T)
                decoded_data = ComicParser.decode_base64_custom(T)

                try:
                    chapter_data = json.loads(decoded_data)
                    # 添加章节标题到返回数据中
                    chapter_data["chapter_title"] = chapter_title

                    result = {
                        "success": True,
                        "data": chapter_data,
                        "chapter_url": chapter_url,
                        "chapter_title": chapter_title,
                    }

                    result = modify_chapter_images_data(result)
                    return result
                except (UnicodeDecodeError, json.JSONDecodeError) as e:
                    retry_count += 1
                    if retry_count == max_retries:
                        return {"error": f"数据解码失败: {str(e)}"}
                    continue

            except Exception as e:
                retry_count += 1
                if retry_count == max_retries:
                    return {"error": f"解析失败: {str(e)}"}

        return {"error": "达到最大重试次数"}


# 同时，在获取章节图片数据的部分，修改图片URL为代理URL
def get_proxy_image_url(original_url, width=600, quality=50, number=0):
    """生成图片代理URL"""
    from urllib.parse import quote

    proxy_url = f"/image/proxy?url={quote(original_url)}&width={width}&quality={quality}&{number}"
    return proxy_url


# 在返回章节数据时，修改图片URL
def modify_chapter_images_data(chapter_data):
    """修改章节数据中的图片URL为代理URL"""
    if chapter_data.get("success") and "data" in chapter_data:
        pictures = chapter_data["data"].get("picture", [])
        number = 0
        for pic in pictures:
            number += 1
            if "url" in pic:
                pic["original_url"] = pic["url"]  # 保留原始URL
                pic["url"] = get_proxy_image_url(
                    pic["url"], number=number
                )  # 替换为代理URL
    return chapter_data


class ComicSearch:
    def __init__(self):
        self.base_url = "https://m.ac.qq.com"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }

    def _check_has_next(
        self, keyword: str, current_page: int, current_count: int
    ) -> bool:
        """
        检查是否有下一页

        Args:
            keyword: 搜索关键词
            current_page: 当前页码
            current_count: 当前页结果数量

        Returns:
            是否有下一页
        """
        try:
            # 直接请求下一页看看是否有内容
            timestamp = int(time.time() * 1000)
            params = {
                "_t": timestamp,
                "word": keyword,
                "page": current_page + 1,
                "pageSize": 10,
                "style": "items",
            }

            url = f"{self.base_url}/search/result?{urlencode(params)}"
            response = requests.get(url, headers=self.headers, timeout=5)
            response.encoding = "utf-8"

            if response.status_code == 200:
                next_results = self._parse_search_results(response.text)
                return len(next_results) > 0
            else:
                return False

        except:
            # 如果请求失败，认为没有下一页
            return False

    def search_comics_direct(self, keyword: str, page: int = 1) -> Dict:
        """
        直接搜索漫画，返回API原始结果

        Args:
            keyword: 搜索关键词
            page: 页码

        Returns:
            搜索结果的原始数据
        """
        try:
            timestamp = int(time.time() * 1000)
            params = {
                "_t": timestamp,
                "word": keyword,
                "page": page,
                "pageSize": 10,  # 可以适当调大一些
                "style": "items",
            }

            url = f"{self.base_url}/search/result?{urlencode(params)}"
            print(f"请求搜索URL: {url}")

            response = requests.get(url, headers=self.headers, timeout=10)
            response.encoding = "utf-8"

            if response.status_code != 200:
                return {
                    "error": f"搜索请求失败，状态码: {response.status_code}",
                    "keyword": keyword,
                    "page": page,
                    "results": [],
                }

            # 解析结果
            results = self._parse_search_results(response.text)
            has_next = self._check_has_next(keyword, page, len(results))

            return {
                "keyword": keyword,
                "page": page,
                "total_results": len(results),
                "results": results,
                "has_more": has_next,  # 简单判断是否还有更多结果
            }

        except Exception as e:
            return {
                "error": f"搜索异常: {str(e)}",
                "keyword": keyword,
                "page": page,
                "results": [],
            }

    def _parse_search_results(self, html: str) -> List[Dict]:
        """
        解析搜索结果HTML
        """
        comics = []
        comic_pattern = r'<li class="comic-item">(.*?)</li>'
        comic_matches = re.findall(comic_pattern, html, re.DOTALL)

        print(f"解析到 {len(comic_matches)} 个漫画条目")

        for comic_html in comic_matches:
            comic = self._parse_single_comic(comic_html)
            if comic:
                comics.append(comic)

        return comics

    def _parse_single_comic(self, comic_html: str) -> Optional[Dict]:
        """
        解析单个漫画信息
        """
        try:
            # 提取漫画ID和链接
            link_match = re.search(r'href="/comic/index/id/(\d+)"', comic_html)
            if not link_match:
                return None

            comic_id = link_match.group(1)

            # 提取标题
            title_match = re.search(
                r'<strong class="comic-title">(.*?)</strong>', comic_html
            )
            title = title_match.group(1).strip() if title_match else "未知标题"

            # 提取封面图片
            cover_match = re.search(r'<img class="cover-image" src="(.*?)"', comic_html)
            cover_url = cover_match.group(1) if cover_match else None
            if cover_url and cover_url.startswith("//"):
                cover_url = "https:" + cover_url

            # 提取更新日期
            update_match = re.search(
                r'<small class="comic-update">(.*?)</small>', comic_html
            )
            update_date = update_match.group(1).strip() if update_match else "未知"

            # 提取标签
            tag_match = re.search(r'<small class="comic-tag">(.*?)</small>', comic_html)
            tags = tag_match.group(1).strip() if tag_match else ""

            # 提取描述
            desc_match = re.search(
                r'<small class="comic-desc">(.*?)</small>', comic_html, re.DOTALL
            )
            description = desc_match.group(1).strip() if desc_match else ""

            return {
                "comic_id": comic_id,
                "title": title,
                "cover_url": cover_url,
                "update_date": update_date,
                "tags": tags,
                "description": description,
                "detail_url": f"{self.base_url}/comic/index/id/{comic_id}",
            }

        except Exception as e:
            print(f"解析漫画信息异常: {e}")
            return None


# 全局搜索实例
comic_searcher = ComicSearch()


# Flask路由
@app.route("/")
def index():
    return "it works!"


@app.route("/comic/<comic_id>")
@app.route("/comic/<comic_id>/")
def get_comic_info(comic_id: str):
    """获取漫画信息接口"""
    try:
        info = ComicParser.get_comic_info(comic_id)
        return jsonify(info)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/comic/<comic_id>/chapter/<int:chapter_number>")
def get_specific_chapter(comic_id: str, chapter_number: int):
    """获取特定章节信息"""
    try:
        comic_info = ComicParser.get_comic_info(comic_id)
        if "error" in comic_info:
            return jsonify(comic_info), 500

        # 查找指定章节
        target_chapter = None
        for chapter in comic_info["chapters"]:
            if chapter["number"] == chapter_number:
                target_chapter = chapter
                break

        if not target_chapter:
            return jsonify({"error": f"未找到第 {chapter_number} 章"}), 404

        # 获取章节图片数据
        images_data = ComicParser.get_chapter_images(target_chapter["link"])

        return jsonify(
            {
                "comic_info": {
                    "comic_id": comic_id,
                    "title": comic_info["title"],
                },
                "chapter_info": target_chapter,
                "images_data": images_data,
            }
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/search/<value>")
@app.get("/search/<value>/")
@app.get("/search/<value>/<int:client_page>")
def search_comics(value: str = "", client_page: Optional[int] = 1):
    """搜索漫画接口"""
    keyword = decode_search_value(value)

    if client_page < 1:
        return jsonify({"error": "页码必须大于0"}), 400

    try:
        # 直接返回API请求结果
        search_result = comic_searcher.search_comics_direct(keyword, client_page)
        return jsonify(search_result)

    except Exception as e:
        return jsonify({"error": f"搜索失败: {str(e)}"}), 500


# 在Flask路由部分添加图片代理接口
@app.route("/image/proxy")
def image_proxy():
    """图片代理接口，处理图片尺寸和质量"""
    try:
        image_url = request.args.get("url")
        if not image_url:
            return jsonify({"error": "缺少url参数"}), 400

        # 设置目标宽度和图片质量
        target_width = int(request.args.get("width", 600))
        quality = int(request.args.get("quality", 50))

        # 下载原始图片
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Referer": "https://ac.qq.com/",
        }

        resp = requests.get(image_url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return jsonify({"error": f"图片下载失败: {resp.status_code}"}), 500

        # 处理图片
        original_image = Image.open(BytesIO(resp.content))

        # 计算新高度，保持宽高比
        width_percent = target_width / float(original_image.size[0])
        target_height = int(float(original_image.size[1]) * float(width_percent))

        # 调整图片尺寸
        resized_image = original_image.resize(
            (target_width, target_height), Image.Resampling.LANCZOS
        )

        # 保存为JPEG格式并调整质量
        output_buffer = BytesIO()
        if original_image.mode in ("RGBA", "LA", "P"):
            # 如果图片有透明度，转换为RGB
            background = Image.new("RGB", resized_image.size, (255, 255, 255))
            background.paste(
                resized_image,
                mask=(
                    resized_image.split()[-1] if resized_image.mode == "RGBA" else None
                ),
            )
            resized_image = background

        resized_image.save(output_buffer, format="JPEG", quality=quality, optimize=True)
        output_buffer.seek(0)

        # 返回处理后的图片
        return Response(
            output_buffer.getvalue(),
            mimetype="image/jpeg",
            headers={
                "Cache-Control": "public, max-age=86400",  # 缓存1天
                "Content-Disposition": "inline",
            },
        )

    except Exception as e:
        logging.error(f"图片处理失败: {str(e)}")
        return jsonify({"error": f"图片处理失败: {str(e)}"}), 500


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
