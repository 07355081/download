"""
RootData 融资信息爬虫
从 https://www.rootdata.com/Fundraising 获取加密货币项目融资数据
"""

import requests
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime
import time
import os
from pathlib import Path
from typing import List, Dict, Optional, Set, Tuple
import re
import argparse
from urllib.parse import unquote

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT = os.path.join("cache", "fundraising_data.csv")


def project_name_from_url(project_url: str) -> str:
    if not project_url:
        return ""
    match = re.search(r"/projects/detail/([^?]+)", project_url)
    if not match:
        return ""
    return unquote(match.group(1)).replace("-", " ").replace("%20", " ").strip()


def clean_project_label(label: str) -> str:
    label = (label or "").strip()
    if len(label) <= 2:
        return label
    match = re.match(r"^(.+?)([A-Z]{2,6})$", label)
    if match and len(match.group(1)) >= 3:
        return match.group(1).strip()
    return label


def clean_investor_name(name: str) -> str:
    """去掉 RootData 头像首字母粘连：BBlueYard → BlueYard；保留 AAVE 等全大写简称。"""
    s = (name or "").strip()
    if len(s) < 2 or not s[0].isalpha():
        return s
    if s[0].lower() != s[1].lower():
        return s
    rest = s[1:]
    if rest.isupper() and " " not in rest and len(rest) <= 6:
        return s
    return rest


ROOTDATA_TAG_HINTS = re.compile(
    r"\b(DeFi|CeFi|CEX|NFT|GameFi|Gaming|Games|DAO|"
    r"Layer\s*1|Layer\s*2|Layer1|Layer2|L1|L2|"
    r"RWA|DePIN|Wallet|Tools?|AI|Infra(?:structure)?|"
    r"Social|Bridge|Lending|Derivatives|Payment)\b",
    re.IGNORECASE,
)


def fetch_project_tags(project_url: str, session: Optional[requests.Session] = None) -> str:
    """从 RootData 项目页提取标签文本，供赛道归类。"""
    if not project_url:
        return ""
    http = session or requests
    try:
        response = http.get(
            project_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.rootdata.com/",
            },
            timeout=20,
        )
        response.encoding = "utf-8"
        response.raise_for_status()
        return " ".join(sorted(set(ROOTDATA_TAG_HINTS.findall(response.text))))
    except requests.RequestException:
        return ""


def project_display_name(
    project_name: str,
    project_url: str = "",
    description: str = "",
) -> str:
    """优先 URL 项目名，避免表格链接文本被截成单字符。"""
    from_url = project_name_from_url(project_url)
    desc = clean_project_label(description)
    name = clean_project_label(project_name)
    if from_url:
        return from_url
    if desc and (not name or len(name) <= 2):
        return desc
    return name or desc or "未披露项目"


def parse_date_column(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, format="mixed", errors="coerce")


def normalize_date_column(df: pd.DataFrame, col: str = "date") -> pd.DataFrame:
    """将可解析日期统一为 YYYY-MM-DD，便于筛选与排序。"""
    out = df.copy()
    parsed = parse_date_column(out[col])
    mask = parsed.notna()
    out.loc[mask, col] = parsed.loc[mask].dt.strftime("%Y-%m-%d")
    return out


def sort_df_by_date_desc(df: pd.DataFrame, col: str = "date") -> pd.DataFrame:
    """按日期从新到旧排序；无法解析的日期排在最后。"""
    out = df.copy()
    out["_sort_date"] = parse_date_column(out[col])
    out = out.sort_values("_sort_date", ascending=False, na_position="last")
    return out.drop(columns="_sort_date")


def resort_csv(path: Path) -> None:
    df = pd.read_csv(path, encoding="utf-8-sig")
    if "date_original" in df.columns:
        reparsed = df["date_original"].apply(parse_date_string)
        mask = reparsed.notna()
        df.loc[mask, "date"] = reparsed.loc[mask]
    df = normalize_date_column(df)
    before = len(df)
    df = merge_same_day_records(df)
    if before != len(df):
        print(f"合并同项目同日: 移除 {before - len(df)} 条")
    if "investors" in df.columns:
        df["investors"] = df["investors"].apply(
            lambda x: ", ".join(x) if isinstance(x, list) else x
        )
    df = sort_df_by_date_desc(df)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    parsed = parse_date_column(df["date"])
    valid = parsed.notna().sum()
    print(f"已重排: {path}")
    print(f"共 {len(df)} 条，可解析日期 {valid} 条")
    if valid:
        print(f"日期范围: {parsed.min().date()} ~ {parsed.max().date()}")


def resolve_yearless_date(
    month: int,
    day: int,
    reference: Optional[datetime] = None,
) -> datetime:
    """无年份月日：默认当年；若落在 reference 之后则回退一年。"""
    ref = reference or datetime.now()
    today = ref.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        candidate = datetime(ref.year, month, day)
    except ValueError as exc:
        raise exc
    if candidate.date() > today.date():
        candidate = datetime(ref.year - 1, month, day)
    return candidate


def parse_date_string(
    date_str: str,
    reference: Optional[datetime] = None,
) -> Optional[str]:
    """解析 RootData 日期文本为 YYYY-MM-DD；无年份时避免解析到未来。"""
    if not date_str or str(date_str).strip() in ("", "--", "nan"):
        return None

    date_str = str(date_str).strip()
    ref = reference or datetime.now()

    if re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        return date_str

    zh_match = re.match(r"^(\d{1,2})\s*月\s*(\d{1,2})(?:\s*日)?$", date_str)
    if zh_match:
        try:
            return resolve_yearless_date(
                int(zh_match.group(1)),
                int(zh_match.group(2)),
                ref,
            ).strftime("%Y-%m-%d")
        except ValueError:
            pass

    for month_fmt in ("%b %d", "%B %d"):
        try:
            month_day = datetime.strptime(date_str, month_fmt)
            return resolve_yearless_date(
                month_day.month,
                month_day.day,
                ref,
            ).strftime("%Y-%m-%d")
        except ValueError:
            continue

    if re.match(r"^\d{2}-\d{2}$", date_str):
        try:
            month, day = map(int, date_str.split("-"))
            return resolve_yearless_date(month, day, ref).strftime("%Y-%m-%d")
        except ValueError:
            pass

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", f"{ref.year}-%m-%d", f"{ref.year}/%m/%d"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    return None


def _parse_investors(value) -> List[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def _is_empty_round(value) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return True
    text = str(value).strip()
    return not text or text == "--"


def _amount_value(record: Dict) -> Optional[float]:
    amount = record.get("amount")
    if amount is not None and not pd.isna(amount):
        try:
            val = float(amount)
            if val > 0:
                return val
        except (TypeError, ValueError):
            pass
    original = record.get("amount_original")
    if original is None or pd.isna(original):
        return None
    text = str(original).strip()
    if not text or text == "--":
        return None
    match = re.match(r"[\$¥￥€£]?\s*([\d.]+)\s*([KMBT])?", text)
    if not match:
        return None
    val = float(match.group(1))
    unit = match.group(2) or ""
    multipliers = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
    return val * multipliers.get(unit, 1)


def _date_key_part(date_val) -> str:
    parsed = parse_date_column(pd.Series([date_val])).iloc[0]
    if pd.notna(parsed):
        return parsed.strftime("%Y-%m-%d")
    return str(date_val or "")


def _same_day_group_key(record: Dict) -> str:
    project = record.get("project_url") or record.get("project_name", "")
    return f"{project}|{_date_key_part(record.get('date'))}"


def _should_merge_same_day(rows: List[Dict]) -> bool:
    """同日多行：仅当不存在多个不同有效金额时合并（保留同日多轮融资）。"""
    amounts = {_amount_value(row) for row in rows}
    distinct = {value for value in amounts if value is not None}
    return len(distinct) <= 1


def _merge_same_day_rows(rows: List[Dict]) -> Dict:
    """合并同项目同日的互补行（金额/轮次/投资方分散在不同行）。"""
    best = dict(rows[0])
    best_amount_row = max(rows, key=lambda row: _amount_value(row) or 0.0)

    for field in (
        "amount",
        "amount_currency",
        "amount_original",
        "valuation",
        "valuation_currency",
        "valuation_original",
    ):
        if _amount_value(best_amount_row) is not None:
            best[field] = best_amount_row.get(field)

    for row in rows:
        if not _is_empty_round(row.get("round")):
            best["round"] = row.get("round")
            break

    investors: List[str] = []
    for row in rows:
        for inv in _parse_investors(row.get("investors")):
            if inv not in investors:
                investors.append(inv)
    best["investors"] = investors
    best["investor_count"] = len(investors)

    names = [
        project_name_from_url(row.get("project_url", ""))
        or str(row.get("project_name") or "").strip()
        for row in rows
    ]
    best["project_name"] = max(names, key=len) if names else best.get("project_name", "")

    descriptions = [str(row.get("description") or "").strip() for row in rows if row.get("description")]
    if descriptions:
        best["description"] = max(descriptions, key=len)

    originals = [str(row.get("date_original") or "").strip() for row in rows if row.get("date_original")]
    if originals:
        best["date_original"] = originals[0]

    return best


def merge_same_day_records(df: pd.DataFrame) -> pd.DataFrame:
    """合并同项目、同日期且仅有一个有效金额的拆分行。"""
    if df.empty:
        return df

    work = normalize_date_column(df.copy())
    grouped: List[Dict] = []
    merge_keys = work.apply(
        lambda row: _same_day_group_key(row.to_dict()),
        axis=1,
    )

    for _, grp in work.groupby(merge_keys, sort=False):
        rows = [row.to_dict() for _, row in grp.iterrows()]
        if len(rows) == 1 or not _should_merge_same_day(rows):
            grouped.extend(rows)
        else:
            grouped.append(_merge_same_day_rows(rows))

    return pd.DataFrame(grouped)


def record_key(record: Dict) -> str:
    """用于去重的记录指纹：项目 + 日期 + 金额（不含轮次、投资方）。"""
    parts = [
        record.get("project_url") or record.get("project_name", ""),
        _date_key_part(record.get("date")),
        str(record.get("amount_original", "") or "--"),
    ]
    return "|".join(parts)


def record_key_from_row(row: pd.Series) -> str:
    return record_key(row.to_dict())


def load_existing_csv(path: str) -> Tuple[pd.DataFrame, Set[str]]:
    if not os.path.isfile(path):
        return pd.DataFrame(), set()
    df = pd.read_csv(path, encoding="utf-8-sig")
    df = merge_same_day_records(df)
    keys = {record_key_from_row(row) for _, row in df.iterrows()}
    return df, keys


def merge_and_save(existing_df: pd.DataFrame, new_records: List[Dict], path: str) -> int:
    if not new_records and existing_df.empty:
        print("没有数据可保存")
        return 0

    new_df = pd.DataFrame(new_records)
    if "investors" in new_df.columns:
        new_df["investors"] = new_df["investors"].apply(
            lambda x: ", ".join(x) if isinstance(x, list) else x
        )

    if existing_df.empty:
        combined = new_df
    else:
        combined = pd.concat([existing_df, new_df], ignore_index=True)

    combined["_key"] = combined.apply(record_key_from_row, axis=1)
    before = len(combined)
    combined = combined.drop_duplicates(subset="_key", keep="first").drop(columns="_key")
    if before != len(combined):
        print(f"去重后移除 {before - len(combined)} 条重复记录")

    before_merge = len(combined)
    combined = merge_same_day_records(combined)
    if before_merge != len(combined):
        print(f"合并同项目同日: 移除 {before_merge - len(combined)} 条")

    if "investors" in combined.columns:
        combined["investors"] = combined["investors"].apply(
            lambda x: ", ".join(x) if isinstance(x, list) else x
        )

    if "date" in combined.columns:
        combined = normalize_date_column(combined)
        combined = sort_df_by_date_desc(combined)

    combined.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"数据已保存到 {path}")
    print(f"共 {len(combined)} 条记录（本次新增 {len(new_records)} 条）")
    return len(new_records)


class RootDataScraper:
    """RootData 融资信息爬虫类"""
    
    def __init__(self):
        self.base_url = "https://www.rootdata.com/Fundraising"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Referer': 'https://www.rootdata.com/',
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        
    def fetch_page(self, page: int = 1) -> Optional[str]:
        """
        获取指定页面的HTML内容
        
        Args:
            page: 页码
            
        Returns:
            HTML内容字符串,失败返回None
        """
        try:
            # RootData 可能使用AJAX加载,需要检查实际的API端点
            # 首先尝试直接访问页面
            params = {
                'page': page
            }
            response = self.session.get(self.base_url, params=params, timeout=30)
            # RootData 页面包含中文日期（如“3月31”），显式指定 UTF-8 避免乱码导致日期解析失败
            response.encoding = 'utf-8'
            response.raise_for_status()
            return response.text
        except requests.RequestException as e:
            print(f"获取第 {page} 页失败: {e}")
            return None
    
    def parse_amount(self, amount_str: str) -> Dict[str, any]:
        """
        解析融资金额字符串
        
        Args:
            amount_str: 金额字符串,如 "$ 4.25 M", "¥ 1.78 B"
            
        Returns:
            包含货币、金额和原始字符串的字典
        """
        if not amount_str or amount_str.strip() == '--':
            return {'currency': None, 'amount': None, 'original': amount_str}
        
        # 提取货币符号和金额（含全角 ￥）
        match = re.match(r'([¥￥$€£])\s*([\d.]+)\s*([KMBT])', amount_str.strip())
        if match:
            currency_symbol = match.group(1)
            amount = float(match.group(2))
            unit = match.group(3)
            
            # 转换单位
            multipliers = {'K': 1e3, 'M': 1e6, 'B': 1e9, 'T': 1e12}
            amount_value = amount * multipliers.get(unit, 1)
            
            # RootData 上 ¥/￥ 多为人民币；$ € £ 为常见外币
            currency_map = {'$': 'USD', '¥': 'CNY', '￥': 'CNY', '€': 'EUR', '£': 'GBP'}
            currency = currency_map.get(currency_symbol, currency_symbol)
            
            return {
                'currency': currency,
                'amount': amount_value,
                'original': amount_str
            }
        
        return {'currency': None, 'amount': None, 'original': amount_str}
    
    def parse_date(self, date_str: str) -> Optional[str]:
        return parse_date_string(date_str)
    
    def parse_table(self, html: str) -> List[Dict]:
        """
        解析HTML表格,提取融资信息
        
        Args:
            html: HTML内容
            
        Returns:
            融资记录列表
        """
        soup = BeautifulSoup(html, 'html.parser')
        records = []
        
        # 查找表格
        # 注意: 需要根据实际的HTML结构调整选择器
        table = soup.find('table')
        if not table:
            print("未找到表格元素")
            return records
        
        # 查找所有数据行
        rows = table.find_all('tr')[1:]  # 跳过表头
        
        for row in rows:
            cols = row.find_all('td')
            if len(cols) < 6:
                continue
            
            try:
                # 提取项目信息
                project_cell = cols[0]
                # 项目列通常包含多个链接（logo 链接可能无文本），优先取首个有文本的链接
                project_links = project_cell.find_all('a')
                project_link = None
                for link in project_links:
                    href = link.get("href", "")
                    if "/projects/detail/" in href:
                        project_link = link
                        break
                if project_link is None and project_links:
                    project_link = project_links[0]
                project_url = project_link["href"] if project_link and "href" in project_link.attrs else ""
                cell_text = project_cell.get_text(strip=True)
                link_text = project_link.get_text(strip=True) if project_link else ""
                project_description = cell_text.replace(link_text, "", 1).strip() if link_text else cell_text
                project_name = project_display_name(
                    link_text,
                    f"https://www.rootdata.com{project_url}" if project_url else "",
                    project_description,
                )
                
                # 提取其他字段
                round_type = cols[1].text.strip()
                amount_str = cols[2].text.strip()
                valuation_str = cols[3].text.strip()
                date_str = cols[4].text.strip()
                
                # 提取投资者
                investors_cell = cols[6] if len(cols) > 6 else None
                investors = []
                if investors_cell:
                    investor_links = investors_cell.find_all('a')
                    investors = [
                        clean_investor_name(inv.text.strip())
                        for inv in investor_links
                        if inv.text.strip()
                    ]
                    investors = [name for name in investors if name]
                
                # 解析金额和日期
                amount_info = self.parse_amount(amount_str)
                valuation_info = self.parse_amount(valuation_str)
                parsed_date = self.parse_date(date_str)
                
                record = {
                    'project_name': project_name,
                    'project_url': f"https://www.rootdata.com{project_url}" if project_url else '',
                    'description': project_description,
                    'round': round_type,
                    'amount': amount_info['amount'],
                    'amount_currency': amount_info['currency'],
                    'amount_original': amount_info['original'],
                    'valuation': valuation_info['amount'],
                    'valuation_currency': valuation_info['currency'],
                    'valuation_original': valuation_info['original'],
                    'date': parsed_date,
                    'date_original': date_str,
                    'investors': investors,
                    'investor_count': len(investors)
                }
                
                records.append(record)
                
            except Exception as e:
                print(f"解析行时出错: {e}")
                continue
        
        return records
    
    def scrape_all_pages(
        self,
        max_pages: int = 500,
        delay: int = 2,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        auto_stop: bool = True,
        existing_keys: Optional[Set[str]] = None,
        stop_on_duplicates: bool = True,
    ) -> List[Dict]:
        """
        爬取页面数据，支持日期筛选与增量去重。

        站点按时间倒序分页时，若已有本地数据，连续多页均为已存在记录则提前停止。
        """
        all_records: List[Dict] = []
        known_keys: Set[str] = set(existing_keys or ())
        start_dt = datetime.strptime(start_date, "%Y-%m-%d") if start_date else None
        end_dt = datetime.strptime(end_date, "%Y-%m-%d") if end_date else None
        consecutive_out_of_range = 0
        consecutive_duplicate_pages = 0

        for page in range(1, max_pages + 1):
            print(f"正在爬取第 {page} 页...")
            html = self.fetch_page(page)

            if not html:
                print(f"第 {page} 页获取失败,停止爬取")
                break

            records = self.parse_table(html)
            if not records:
                print(f"第 {page} 页没有数据,停止爬取")
                break

            page_valid_records: List[Dict] = []
            page_out_of_range = 0
            page_skipped_existing = 0
            page_considered = 0

            for record in records:
                key = record_key(record)
                if key in known_keys:
                    page_skipped_existing += 1
                    continue

                if not record.get("date"):
                    page_valid_records.append(record)
                    known_keys.add(key)
                    continue

                try:
                    record_date = datetime.strptime(record["date"], "%Y-%m-%d")
                except ValueError:
                    page_valid_records.append(record)
                    known_keys.add(key)
                    continue

                page_considered += 1
                in_range = True
                if start_dt and record_date < start_dt:
                    in_range = False
                    page_out_of_range += 1
                if end_dt and record_date > end_dt:
                    in_range = False
                    page_out_of_range += 1

                if in_range:
                    page_valid_records.append(record)
                    known_keys.add(key)

            all_records.extend(page_valid_records)
            print(
                f"第 {page} 页: 解析 {len(records)} 条，"
                f"新增 {len(page_valid_records)} 条，"
                f"跳过已有 {page_skipped_existing} 条"
            )

            if (
                auto_stop
                and start_date
                and page_considered > 0
                and page_out_of_range == page_considered
            ):
                consecutive_out_of_range += 1
                print(f"第 {page} 页日期均早于开始日期 ({consecutive_out_of_range}/2)")
                if consecutive_out_of_range >= 2:
                    print("连续 2 页均早于开始日期，停止爬取")
                    break
            else:
                consecutive_out_of_range = 0

            if (
                stop_on_duplicates
                and known_keys
                and page_skipped_existing == len(records)
            ):
                consecutive_duplicate_pages += 1
                print(f"第 {page} 页全部为已有记录 ({consecutive_duplicate_pages}/2)")
                if consecutive_duplicate_pages >= 2:
                    print("连续 2 页均为已有记录，增量更新完成")
                    break
            else:
                consecutive_duplicate_pages = 0

            if page < max_pages:
                time.sleep(delay)

        return all_records
    
    def filter_by_date(self, records: List[Dict], 
                       start_date: Optional[str] = None, 
                       end_date: Optional[str] = None) -> List[Dict]:
        """
        按日期筛选记录
        
        Args:
            records: 记录列表
            start_date: 开始日期 YYYY-MM-DD
            end_date: 结束日期 YYYY-MM-DD
            
        Returns:
            筛选后的记录列表
        """
        filtered = []
        
        for record in records:
            if not record.get('date'):
                continue
            
            try:
                record_date = datetime.strptime(record['date'], '%Y-%m-%d')
                
                # 检查日期范围
                if start_date:
                    start = datetime.strptime(start_date, '%Y-%m-%d')
                    if record_date < start:
                        continue
                
                if end_date:
                    end = datetime.strptime(end_date, '%Y-%m-%d')
                    if record_date > end:
                        continue
                
                filtered.append(record)
                
            except ValueError:
                continue
        
        return filtered
    
def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="RootData 融资信息爬虫（默认全历史增量）")
    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="开始日期 YYYY-MM-DD；默认不限制（全历史）",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default=datetime.now().strftime("%Y-%m-%d"),
        help="结束日期 YYYY-MM-DD，默认为今天",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=500,
        help="最大爬取页数，默认 500（全量首次可适当加大）",
    )
    parser.add_argument(
        "--delay",
        type=int,
        default=2,
        help="每页请求间隔秒数，默认 2 秒",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=DEFAULT_OUTPUT,
        help=f"输出 CSV，默认 {DEFAULT_OUTPUT}（与已有文件合并去重）",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="全量翻页：不因连续「整页已存在」而提前停止（补历史缺口时用）",
    )
    parser.add_argument(
        "--resort",
        action="store_true",
        help="重排已有 CSV：按 date_original 修正日期、合并同项目同日、统一 YYYY-MM-DD",
    )

    args = parser.parse_args()

    print("=" * 70)
    print("RootData 融资信息爬虫")
    print("=" * 70)
    output_path = args.output if os.path.isabs(args.output) else os.path.join(BASE_DIR, args.output)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    if args.resort:
        if not os.path.isfile(output_path):
            print(f"文件不存在: {output_path}")
            return
        resort_csv(Path(output_path))
        return

    existing_df, existing_keys = load_existing_csv(output_path)
    date_range = (
        f"{args.start_date or '最早'} 到 {args.end_date}"
    )
    print(f"\n日期范围: {date_range}")
    print(f"最大页数: {args.max_pages}")
    print(f"请求延迟: {args.delay} 秒")
    print(f"输出文件: {output_path}")
    if existing_keys:
        print(f"已有记录: {len(existing_keys)} 条（按项目/日期/金额去重，已存在则跳过）")
    else:
        print("已有记录: 无（将全量爬取直至无数据或达到最大页数）")
    if args.full:
        print("模式: 全量翻页（--full）")
    else:
        print("模式: 增量（连续 2 页均为已有记录时停止）")

    scraper = RootDataScraper()

    print("\n开始爬取数据...")
    records = scraper.scrape_all_pages(
        max_pages=args.max_pages,
        delay=args.delay,
        start_date=args.start_date,
        end_date=args.end_date,
        auto_stop=True,
        existing_keys=existing_keys,
        stop_on_duplicates=not args.full,
    )

    if not records:
        if existing_df.empty:
            print("\n未获取到任何数据,可能原因:")
            print("1. 网站可能使用 JavaScript 动态加载")
            print("2. 网站结构发生变化，需要更新解析代码")
            print("3. 被反爬虫机制拦截（可增加 --delay）")
        else:
            print("\n本次无新增记录，本地数据已是最新。")
        return

    print(f"\nOK: 本次新抓取 {len(records)} 条")
    merge_and_save(existing_df, records, output_path)
    
    # 统计信息
    print("\n" + "=" * 70)
    print("数据统计")
    print("=" * 70)
    
    combined_df = pd.read_csv(output_path, encoding="utf-8-sig")
    if not combined_df.empty:
        df = combined_df
    elif records:
        df = pd.DataFrame(records)
    else:
        df = pd.DataFrame()

    if not df.empty:
        
        # 日期范围
        if 'date' in df.columns:
            valid_dates = df[df['date'].notna()]['date']
            if len(valid_dates) > 0:
                print(f"\n实际日期范围: {valid_dates.min()} 到 {valid_dates.max()}")
        
        # 轮次分布
        if 'round' in df.columns:
            print("\n轮次分布 (前10):")
            round_counts = df['round'].value_counts()
            for round_type, count in round_counts.head(10).items():
                print(f"  {round_type}: {count}")
        
        # 融资金额统计
        valid_amounts = df[df['amount'].notna()]
        if len(valid_amounts) > 0:
            print(f"\n有金额记录: {len(valid_amounts)} 条")
            total = valid_amounts['amount'].sum()
            avg = valid_amounts['amount'].mean()
            print(f"总融资金额: ${total:,.0f}")
            print(f"平均融资金额: ${avg:,.0f}")


if __name__ == '__main__':
    main()
