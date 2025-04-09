import openai
import requests
import yaml
import time
import logging
import json
import os
import textwrap

# ------------------------------------------------------------
# 日志配置：将日志级别设为 INFO，并统一日志格式
# ------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ------------------------------------------------------------
# 读取环境变量（env.yml）以及相关配置
# ------------------------------------------------------------
def read_env(env_file="env.yml"):
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            env_config = yaml.safe_load(f)
            return env_config
    except Exception as e:
        logging.error(f"读取配置文件 {env_file} 失败: {e}")
        return {}

env_config = read_env()
# 从环境中获取 API Key 与 Zotero 的用户信息
BAI_LIAN_API_KEY = env_config.get("Bai_Lian_API_KEY", "")
openai.api_key = BAI_LIAN_API_KEY
openai.api_base = "https://dashscope.aliyuncs.com/compatible-mode/v1"

ZOTERO_USER_ID = env_config.get("Zotero_user_id", "")
ZOTERO_API_KEY = env_config.get("Zotero_API_KEY", "")
ZOTERO_UPLOAD_URL = f"https://api.zotero.org/users/{ZOTERO_USER_ID}/items"

# ------------------------------------------------------------
# 读取配置文件（config.yaml）
# ------------------------------------------------------------
def read_config(config_file="config.yaml"):
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            config_data = yaml.safe_load(f)
            return config_data
    except Exception as e:
        logging.error(f"读取配置文件 {config_file} 时出错: {e}")
        return {}

# ------------------------------------------------------------
# 函数：fetch_crossref
# 作用：调用 CrossRef API 根据查询条件获取文献数据
# ------------------------------------------------------------
def fetch_crossref(query, year_range):
    url = "https://api.crossref.org/works"
    try:
        year_from, year_until = year_range.split('-')
    except ValueError:
        logging.error("出版年份范围格式错误，应为 'YYYY-YYYY'")
        return []

    params = {
        "query": query,
        "filter": f"from-pub-date:{year_from},until-pub-date:{year_until}",
        "rows": 50  # 可根据需要调整获取文献数量
    }
    headers = {
        "User-Agent": "DocumentManager/1.0 (mailto:your_email@example.com)"  # 请替换为你自己的邮箱
    }
    try:
        response = requests.get(url, params=params, headers=headers)
        if response.status_code == 200:
            items = response.json().get('message', {}).get('items', [])
            return items
        else:
            logging.error(f"CrossRef请求失败，状态码：{response.status_code}")
            return []
    except Exception as e:
        logging.error(f"请求 CrossRef 时出错: {e}")
        return []

# ------------------------------------------------------------
# 函数：qwen_api_call
# 作用：调用 Qwen API 生成对话回复（主要用于获取文献相关性评分）
# ------------------------------------------------------------
def qwen_api_call(prompt, model="qwen-max"):
    try:
        completion = openai.ChatCompletion.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.0,  # 保证输出稳定
            top_p=1.0,
            max_tokens=10    # 输出长度，可根据需要调整
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        logging.error(f"Qwen API 调用失败：{e}")
        return ""

# ------------------------------------------------------------
# 函数：ai_filter_qwen
# 作用：利用 Qwen API 对文献与给定方向的相关性进行评分
# ------------------------------------------------------------
def ai_filter_qwen(item, direction, threshold=0.6):
    prompt = textwrap.dedent(f"""
        请判断以下文献是否与“{direction}”密切相关，并请仅返回一个0到1之间的评分（保留两位小数）。

        标题：{item.get('title', '无标题')}
        摘要：{item.get('abstract', '无摘要')}

        评分标准：
        0.00 表示完全不相关，1.00 表示高度相关。
    """)
    response = qwen_api_call(prompt)
    try:
        score = float(response)
        logging.info(f"文献《{item['title'][:30]}...》评分：{score}")
        return score >= threshold
    except ValueError as e:
        logging.error(f"评分解析错误：{e}，返回内容：{response}")
        return False

# ------------------------------------------------------------
# 函数：save_filtered_items
# 作用：将过滤后的文献信息保存到 YAML 文件
# ------------------------------------------------------------
def save_filtered_items(filtered_items, filename="filtered_items.yaml"):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            yaml.safe_dump(filtered_items, f, allow_unicode=True)
        logging.info(f"过滤后的文献信息已保存至 {filename}")
    except Exception as e:
        logging.error(f"保存文件 {filename} 时出错: {e}")

# ------------------------------------------------------------
# 函数：load_filtered_items
# 作用：从 YAML 文件中读取之前筛选好的文献信息
# ------------------------------------------------------------
def load_filtered_items(filename="filtered_items.yaml"):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            items = yaml.safe_load(f)
        if items:
            logging.info(f"成功读取 {len(items)} 篇筛选后的文献。")
            return items
        else:
            logging.info("没有找到任何筛选后的文献。")
            return []
    except Exception as e:
        logging.error(f"读取文件 {filename} 时出错: {e}")
        return []

# ------------------------------------------------------------
# 函数：convert_to_zotero_format
# 作用：转换单个文献信息为符合 Zotero API 要求的数据格式
# ------------------------------------------------------------
def convert_to_zotero_format(item):
    zotero_item = {
        "itemType": "journalArticle",
        "title": item.get("title", "无标题"),
        "abstractNote": item.get("abstract", ""),
        "creators": [],
        "date": str(item.get("date", "")),
        "DOI": item.get("DOI", "")
    }
    for author in item.get("authors", []):
        if isinstance(author, dict):
            if "firstName" in author and "lastName" in author:
                creator = {
                    "creatorType": "author",
                    "firstName": author["firstName"],
                    "lastName": author["lastName"]
                }
            else:
                creator = {"creatorType": "author", "lastName": author.get("name", "").strip()}
            zotero_item["creators"].append(creator)
        elif isinstance(author, str):
            # 如果作者仅为字符串形式
            zotero_item["creators"].append({"creatorType": "author", "lastName": author.strip()})
    return zotero_item

# ------------------------------------------------------------
# 函数：upload_items_to_zotero
# 作用：将转换后的文献数据上传到 Zotero
# ------------------------------------------------------------
def upload_items_to_zotero(items):
    headers = {
        "Zotero-API-Key": ZOTERO_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    zotero_items = [convert_to_zotero_format(item) for item in items]
    payload = json.dumps(zotero_items, ensure_ascii=False)
    try:
        response = requests.post(ZOTERO_UPLOAD_URL, headers=headers, data=payload)
        if response.status_code in (200, 201):
            logging.info("文献上传成功！")
            try:
                resp_json = response.json()
                logging.info("服务器返回的信息：")
                logging.info(json.dumps(resp_json, ensure_ascii=False, indent=2))
            except Exception as err:
                logging.error(f"解析服务器返回数据时出错：{err}")
        else:
            logging.error(f"上传失败，状态码: {response.status_code}")
            logging.error(f"响应内容：{response.text}")
    except Exception as e:
        logging.error(f"上传过程中出现错误：{e}")

# ------------------------------------------------------------
# 主函数：整合跨平台文献筛选与上传流程
# ------------------------------------------------------------
def main():
    # 第一阶段：根据配置文件，从 CrossRef 获取文献并使用 Qwen API 过滤
    config = read_config()
    direction = config.get("direction", "")
    keywords = " ".join(config.get("keywords", []))
    publication_year = config.get("publication_year", "")
    query = f"{direction} {keywords}".strip()
    if not (direction and publication_year):
        logging.error("配置文件缺少 'direction' 或 'publication_year' 参数，请检查 config.yaml。")
        return

    items = fetch_crossref(query, publication_year)
    if not items:
        logging.info("未获取到任何文献。")
        return

    processed_items = []
    for item in items:
        processed = {
            "title": item.get("title", ["无标题"])[0] if item.get("title") else "无标题",
            "abstract": item.get("abstract", "无摘要"),
            "authors": item.get("author", []),
            "date": item.get("issued", {}).get("date-parts", [[""]])[0][0],
            "DOI": item.get("DOI", "")
        }
        processed_items.append(processed)
    logging.info(f"初步获取到 {len(processed_items)} 篇文献。")

    filtered_items = []
    for item in processed_items:
        if ai_filter_qwen(item, direction):
            filtered_items.append(item)
            logging.info(f"文献《{item['title']}》通过 Qwen 过滤。")
        else:
            logging.info(f"文献《{item['title']}》未通过 Qwen 过滤。")
        time.sleep(1)  # 暂停1秒，避免请求过于频繁

    logging.info(f"经过 Qwen 过滤后，剩余文献数量：{len(filtered_items)}")
    save_filtered_items(filtered_items, filename="filtered_items.yaml")

    # 第二阶段：从保存结果中读取文献数据并上传至 Zotero
    items_to_upload = load_filtered_items("filtered_items.yaml")
    if items_to_upload:
        logging.info("开始上传文献到 Zotero ...")
        upload_items_to_zotero(items_to_upload)
    else:
        logging.info("没有可上传的文献。")

if __name__ == "__main__":
    main()
