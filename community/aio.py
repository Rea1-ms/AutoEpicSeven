from __future__ import annotations

import argparse
import base64
import json
import os
import random
import re
import time
from dataclasses import dataclass
from typing import Any

import requests

BASE_URL = "https://sns.zlongame.com/pdapi"
DEFAULT_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "origin": "https://epic7-community.zlongame.com",
    "pd-login-flag": "1",
    "referer": "https://epic7-community.zlongame.com/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    ),
}
SUCCESS_CODE = 0
ALREADY_SIGNED_CODE = 1022
BUY_STOP_CODES = {2100, 2104}
COMMUNITY_COOKIE_NAMESPACE = "1611630374326"
DEFAULT_E7_CREDENTIALS_FILENAME = "e7-credentials.json"


@dataclass
class ApiResult:
    ok: bool
    code: int | None
    message: str
    data: Any = None
    raw: dict[str, Any] | None = None


def decode_jwt_claims(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        padding = "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload + padding).decode("utf-8")
        claims = json.loads(decoded)
        return claims if isinstance(claims, dict) else {}
    except (IndexError, ValueError, json.JSONDecodeError):
        return {}


def build_pd_bf(user_id: str, auth_token: str) -> str:
    claims = decode_jwt_claims(auth_token)
    account_id = str(claims.get("name") or claims.get("id") or user_id)
    return f"{account_id}#0#0##"


def get_token_user_id(auth_token: str) -> str | None:
    claims = decode_jwt_claims(auth_token)
    user_id = claims.get("id") or claims.get("name")
    if user_id is None:
        return None
    return str(user_id)


def get_token_expiry(auth_token: str) -> int | None:
    claims = decode_jwt_claims(auth_token)
    exp = claims.get("exp")
    try:
        return int(exp) if exp is not None else None
    except (TypeError, ValueError):
        return None


def get_default_credentials_path() -> str:
    appdata = os.getenv("APPDATA")
    if appdata:
        return os.path.join(appdata, "aes", DEFAULT_E7_CREDENTIALS_FILENAME)
    return DEFAULT_E7_CREDENTIALS_FILENAME


def _pick_string(mapping: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def load_credentials_file(path: str) -> dict[str, str]:
    try:
        with open(path, "r", encoding="utf-8") as fp:
            raw = json.load(fp)
    except OSError as exc:
        raise ValueError(f"读取凭证文件失败: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"凭证文件不是合法 JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError("凭证文件格式错误：根节点必须是对象")

    auth_token = _pick_string(raw, "token", "auth_token", "authorization")
    pd_did = _pick_string(raw, "pd_did", "pd-did", "pdDid")
    pd_dvid = _pick_string(raw, "pd_dvid", "pd-dvid", "pdDvid")
    user_id = _pick_string(raw, "uid", "user_id", "userId")
    jsessionid = _pick_string(raw, "jsessionid", "jsessionId", "JSESSIONID")
    return {
        "token": auth_token or "",
        "pd_did": pd_did or "",
        "pd_dvid": pd_dvid or "",
        "uid": user_id or "",
        "jsessionid": jsessionid or "",
    }


class EpicSevenCommunityAIO:
    def __init__(
        self,
        user_id: str,
        auth_token: str,
        jsessionid: str | None = None,
        pd_did: str | None = None,
        pd_dvid: str | None = None,
        delay_min: float = 0.6,
        delay_max: float = 1.2,
        timeout: float = 10.0,
    ):
        if not pd_did or not pd_dvid:
            raise ValueError("pd-did 和 pd-dvid 必须显式提供")
        self.user_id = str(user_id)
        self.auth_token = auth_token
        self.delay_min = max(delay_min, 0.0)
        self.delay_max = max(delay_max, self.delay_min)
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self.session.headers.update(
            {
                "authorization": auth_token,
                "pd-did": pd_did,
            }
        )
        self.session.cookies.update(
            {
                "_pd_key": COMMUNITY_COOKIE_NAMESPACE,
                "_pd_ckey": COMMUNITY_COOKIE_NAMESPACE,
                f"_pd_dvid_{COMMUNITY_COOKIE_NAMESPACE}": pd_dvid,
                f"_pd_auth_{COMMUNITY_COOKIE_NAMESPACE}": auth_token,
                "_pd_bf": build_pd_bf(self.user_id, auth_token),
            }
        )
        if jsessionid:
            self.session.cookies.set("JSESSIONID", jsessionid)

    @staticmethod
    def _as_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def delay(self, min_sec: float | None = None, max_sec: float | None = None) -> None:
        lower = self.delay_min if min_sec is None else max(min_sec, 0.0)
        upper = self.delay_max if max_sec is None else max(max_sec, lower)
        if upper <= 0:
            return
        time.sleep(random.uniform(lower, upper))

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> ApiResult:
        url = f"{BASE_URL}{path}"
        try:
            response = self.session.request(
                method=method.upper(),
                url=url,
                params=params,
                data=data,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            return ApiResult(ok=False, code=None, message=str(exc))

        try:
            payload = response.json()
        except ValueError:
            return ApiResult(
                ok=False,
                code=None,
                message=f"服务器返回了非 JSON 内容: {response.text[:200]}",
            )

        code = payload.get("code")
        message = str(payload.get("message", ""))
        return ApiResult(
            ok=code == SUCCESS_CODE,
            code=code if isinstance(code, int) else None,
            message=message,
            data=payload.get("data"),
            raw=payload if isinstance(payload, dict) else None,
        )

    def sign_in(self) -> ApiResult:
        result = self._request_json("GET", "/task/sign")
        if result.ok:
            data = result.data or {}
            print(
                f"✅ [签到成功] {data.get('desc', '签到成功')} "
                f"(经验+{self._as_int(data.get('exp'))})"
            )
            return result
        if result.code == ALREADY_SIGNED_CODE:
            print(f"☑️ [签到提示] {result.message or '今日已签到'}")
            return ApiResult(
                ok=True,
                code=result.code,
                message=result.message or "今日已签到",
                data=result.data,
                raw=result.raw,
            )
        print(f"❌ [签到失败] {result.message or result.raw}")
        return result

    def get_recommend_topics(self, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
        param_candidates = (
            {"pageNo": page, "pageSize": limit, "sortType": 0},
            {"page": page, "pageSize": limit, "sortType": 0},
            {"page": page, "limit": limit, "sortType": 0},
            {"pageNum": page, "pageSize": limit, "sortType": 0},
        )
        last_result: ApiResult | None = None

        for params in param_candidates:
            result = self._request_json(
                "GET",
                "/topic/recommend/topics",
                params=params,
            )
            last_result = result
            if result.ok:
                topics = result.data.get("list", []) if isinstance(result.data, dict) else []
                print(
                    f"🔍 [获取列表] 第 {page} 页抓取到 {len(topics)} 篇帖子 "
                    f"(参数 {params})"
                )
                return [topic for topic in topics if isinstance(topic, dict)]
            if result.message != "分页参数错误":
                break

        print(
            f"⚠️ [获取帖子列表失败] 第 {page} 页: "
            f"{(last_result.message if last_result else '未知错误')}"
        )
        return []

    def get_topic_detail(self, topic_id: int | str) -> ApiResult:
        result = self._request_json("GET", f"/topic/detail/{topic_id}")
        if result.ok:
            print(f"📖 [浏览帖子] ID={topic_id}")
        else:
            print(f"⚠️ [浏览失败] ID={topic_id}: {result.message or result.raw}")
        return result

    def like_topic(self, topic_id: int | str) -> ApiResult:
        result = self._request_json("GET", f"/topic/like/{topic_id}")
        if result.ok:
            print(f"👍 [点赞成功] ID={topic_id}: {result.message or 'success'}")
        else:
            print(f"⚠️ [点赞失败] ID={topic_id}: {result.message or result.raw}")
        return result

    def share_topic(self, topic_id: int | str, way: int = 5) -> ApiResult:
        result = self._request_json(
            "GET",
            f"/topic/share/{topic_id}",
            params={"way": str(way)},
        )
        if result.ok:
            print(f"🔗 [分享成功] ID={topic_id}: {result.message or 'success'}")
        else:
            print(f"⚠️ [分享失败] ID={topic_id}: {result.message or result.raw}")
        return result

    def run_action_tasks(
        self,
        browse_target: int = 3,
        like_target: int = 3,
        share_target: int = 1,
        max_pages: int = 5,
        page_size: int = 20,
    ) -> dict[str, int]:
        print("\n🎯 开始执行社区任务...")
        progress = {"browse": 0, "like": 0, "share": 0}
        seen_topic_ids: set[int | str] = set()

        for page in range(1, max_pages + 1):
            if (
                progress["browse"] >= browse_target
                and progress["like"] >= like_target
                and progress["share"] >= share_target
            ):
                break

            topics = self.get_recommend_topics(page=page, limit=page_size)
            if not topics:
                break

            for topic in topics:
                if (
                    progress["browse"] >= browse_target
                    and progress["like"] >= like_target
                    and progress["share"] >= share_target
                ):
                    break

                topic_id = topic.get("id")
                if not topic_id or topic_id in seen_topic_ids:
                    continue
                seen_topic_ids.add(topic_id)
                author = topic.get("memberNickName") or topic.get("title") or "未知作者"
                print(f"\n--- 处理帖子 {topic_id} [{author}] ---")

                detail_result = self.get_topic_detail(topic_id)
                if not detail_result.ok:
                    self.delay()
                    continue

                detail = detail_result.data if isinstance(detail_result.data, dict) else {}
                if progress["browse"] < browse_target:
                    progress["browse"] += 1
                    print(f"📘 [浏览进度] {progress['browse']}/{browse_target}")
                self.delay()

                if progress["like"] < like_target:
                    if self._as_int(detail.get("isLike")) == 0:
                        like_result = self.like_topic(topic_id)
                        if like_result.ok and "取消" not in like_result.message:
                            progress["like"] += 1
                            print(f"❤️ [点赞进度] {progress['like']}/{like_target}")
                        self.delay()
                    else:
                        print("ℹ️ [点赞跳过] 该帖子已点赞，继续找下一篇。")

                if progress["share"] < share_target:
                    share_result = self.share_topic(topic_id)
                    if share_result.ok:
                        progress["share"] += 1
                        print(f"📤 [分享进度] {progress['share']}/{share_target}")
                    self.delay()

        print(
            "\n📊 [任务汇总] "
            f"浏览 {progress['browse']}/{browse_target} | "
            f"点赞 {progress['like']}/{like_target} | "
            f"分享 {progress['share']}/{share_target}"
        )
        return progress

    def get_goods_list(self, page_size: int = 20, max_pages: int = 3) -> ApiResult:
        merged_goods: list[dict[str, Any]] = []
        latest_data: dict[str, Any] = {}

        for page_no in range(1, max_pages + 1):
            result = self._request_json(
                "GET",
                "/exchange/goodslist",
                params={"pageNo": page_no, "pageSize": page_size},
            )
            if not result.ok:
                return result

            data = result.data if isinstance(result.data, dict) else {}
            latest_data = data
            page_goods = data.get("goodList", [])
            page_goods = [goods for goods in page_goods if isinstance(goods, dict)]
            merged_goods.extend(page_goods)
            if len(page_goods) < page_size:
                break

        latest_data["goodList"] = merged_goods
        return ApiResult(
            ok=True,
            code=SUCCESS_CODE,
            message="success",
            data=latest_data,
            raw={"code": SUCCESS_CODE, "message": "success", "data": latest_data},
        )

    def buy_goods(self, goods_id: int | str) -> ApiResult:
        result = self._request_json(
            "POST",
            "/exchange/buyGoods",
            data={"goodsId": str(goods_id)},
        )
        if result.ok:
            print(f"🛍️ [兑换成功] 商品ID={goods_id}: {result.message or 'success'}")
        else:
            print(f"⚠️ [兑换失败] 商品ID={goods_id}: {result.message or result.raw}")
        return result

    @staticmethod
    def _parse_sale_rule_progress(sale_rule: str | None) -> tuple[int, int] | None:
        if not sale_rule:
            return None
        match = re.search(r"(\d+)\s*/\s*(\d+)\s*$", sale_rule)
        if not match:
            return None
        return int(match.group(1)), int(match.group(2))

    @classmethod
    def _can_attempt_goods(cls, goods: dict[str, Any], points: int) -> bool:
        price = cls._as_int(goods.get("price"))
        if price <= 0 or price > points:
            return False

        goods_status = goods.get("goodsStatus")
        if goods_status not in (None, 2):
            return False

        progress = cls._parse_sale_rule_progress(str(goods.get("saleRule") or ""))
        if progress is not None:
            purchased_count, purchase_limit = progress
            if purchased_count >= purchase_limit:
                return False

        if goods.get("stockRule") == 0 and cls._as_int(goods.get("stock"), default=0) <= 0:
            return False

        return True

    def buy_all_goods(
        self,
        page_size: int = 20,
        max_pages: int = 3,
        max_purchase_attempts: int = 20,
    ) -> dict[str, Any]:
        print("\n🛒 开始自动兑换所有可兑商品...")
        purchased_items: list[str] = []
        last_points = 0
        attempts = 0

        while attempts < max_purchase_attempts:
            goods_result = self.get_goods_list(page_size=page_size, max_pages=max_pages)
            if not goods_result.ok:
                print(f"❌ [拉取商城失败] {goods_result.message or goods_result.raw}")
                break

            data = goods_result.data if isinstance(goods_result.data, dict) else {}
            goods_list = data.get("goodList", [])
            goods_list = [goods for goods in goods_list if isinstance(goods, dict)]
            last_points = self._as_int(data.get("points"))

            if not goods_list:
                print("ℹ️ [兑换结束] 商品列表为空。")
                break

            purchased_this_round = False
            for goods in goods_list:
                if attempts >= max_purchase_attempts:
                    break
                if not self._can_attempt_goods(goods, last_points):
                    continue

                goods_id = goods.get("id")
                if goods_id is None:
                    continue

                goods_name = str(goods.get("goodsName") or f"商品{goods_id}")
                price = self._as_int(goods.get("price"))
                print(
                    f"🧾 [准备兑换] {goods_name} | "
                    f"ID={goods_id} | 价格={price} | 当前积分={last_points}"
                )
                result = self.buy_goods(goods_id)
                attempts += 1

                if result.ok:
                    purchased_items.append(goods_name)
                    purchased_this_round = True
                    self.delay(0.3, 0.8)
                    break

                if result.code in BUY_STOP_CODES:
                    print(f"ℹ️ [跳过商品] {goods_name}: {result.message}")
                else:
                    print(f"⚠️ [兑换异常] {goods_name}: {result.message or result.raw}")
                self.delay(0.1, 0.3)

            if not purchased_this_round:
                if last_points <= 0:
                    print("ℹ️ [兑换结束] 当前积分不足。")
                else:
                    print("ℹ️ [兑换结束] 当前已无可继续兑换的商品。")
                break

        print(
            f"📦 [兑换汇总] 成功兑换 {len(purchased_items)} 次，"
            f"剩余积分约 {last_points}"
        )
        return {
            "count": len(purchased_items),
            "items": purchased_items,
            "final_points": last_points,
        }

    def run(
        self,
        *,
        browse_target: int,
        like_target: int,
        share_target: int,
        topic_pages: int,
        topic_page_size: int,
        goods_pages: int,
        goods_page_size: int,
        skip_actions: bool,
        skip_exchange: bool,
    ) -> None:
        print(f"🚀 开始执行第七史诗社区 AIO 流程，UID={self.user_id}")
        sign_result = self.sign_in()
        sign_ok = sign_result.ok
        if sign_result.message == "当前登陆账号不满足发奖条件":
            print("⛔ [流程终止] 当前社区账号未绑定对应角色，后续任务和兑换已跳过。")
            return
        self.delay()

        action_summary: dict[str, int] | None = None
        if not skip_actions:
            action_summary = self.run_action_tasks(
                browse_target=browse_target,
                like_target=like_target,
                share_target=share_target,
                max_pages=topic_pages,
                page_size=topic_page_size,
            )

        exchange_summary: dict[str, Any] | None = None
        if not skip_exchange:
            exchange_summary = self.buy_all_goods(
                page_size=goods_page_size,
                max_pages=goods_pages,
            )

        print("\n================ 最终结果 ================")
        print(f"签到: {'成功/已签到' if sign_ok else '失败'}")
        if action_summary is not None:
            print(
                "任务: "
                f"浏览 {action_summary['browse']}/{browse_target} | "
                f"点赞 {action_summary['like']}/{like_target} | "
                f"分享 {action_summary['share']}/{share_target}"
            )
        if exchange_summary is not None:
            items = ", ".join(exchange_summary["items"]) if exchange_summary["items"] else "无"
            print(
                f"兑换: {exchange_summary['count']} 次 | "
                f"剩余积分约 {exchange_summary['final_points']} | "
                f"商品: {items}"
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="第七史诗社区 AIO 协议脚本：签到 + 做任务 + 自动兑换所有可兑商品",
    )
    parser.add_argument("--uid", help="可选。手动指定 JWT 内的真实用户 UID；默认自动从 token 解出")
    parser.add_argument("--token", help="社区 Authorization Token，也可通过环境变量 E7_TOKEN 提供")
    parser.add_argument(
        "--credentials-file",
        help=(
            "可选。读取 Electron 登录获取的凭证 JSON（默认尝试 "
            f"{get_default_credentials_path()}，也可用环境变量 E7_CREDENTIALS_FILE 指定）"
        ),
    )
    parser.add_argument("--jsessionid", help="可选。若你抓包里有 JSESSIONID，可一并传入提升稳定性")
    parser.add_argument("--pd-did", help="必填。设备指纹 pd-did，也可通过环境变量 E7_PD_DID 提供")
    parser.add_argument(
        "--pd-dvid",
        help=(
            "必填。Cookie _pd_dvid_1611630374326 的值，"
            "也可通过环境变量 E7_PD_DVID 提供"
        ),
    )
    parser.add_argument("--browse-target", type=int, default=3, help="浏览帖子目标数，默认 3")
    parser.add_argument("--like-target", type=int, default=3, help="点赞帖子目标数，默认 3")
    parser.add_argument("--share-target", type=int, default=1, help="分享帖子目标数，默认 1")
    parser.add_argument("--topic-pages", type=int, default=5, help="最多扫描多少页推荐帖子，默认 5")
    parser.add_argument("--topic-page-size", type=int, default=20, help="每页拉取多少帖子，默认 20")
    parser.add_argument("--goods-pages", type=int, default=3, help="最多扫描多少页商品，默认 3")
    parser.add_argument("--goods-page-size", type=int, default=20, help="每页拉取多少商品，默认 20")
    parser.add_argument("--delay-min", type=float, default=0.6, help="请求间最小延迟秒数，默认 0.6")
    parser.add_argument("--delay-max", type=float, default=1.2, help="请求间最大延迟秒数，默认 1.2")
    parser.add_argument("--timeout", type=float, default=10.0, help="单次请求超时秒数，默认 10")
    parser.add_argument("--skip-actions", action="store_true", help="只签到+兑换，不做浏览/点赞/分享")
    parser.add_argument("--skip-exchange", action="store_true", help="只签到+做任务，不自动兑换商品")
    return parser


def resolve_credentials(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> tuple[str, str, str, str, str | None]:
    credentials_path = args.credentials_file or os.getenv("E7_CREDENTIALS_FILE")
    if not credentials_path:
        default_path = get_default_credentials_path()
        if os.path.exists(default_path):
            credentials_path = default_path

    file_credentials: dict[str, str] = {}
    if credentials_path:
        try:
            file_credentials = load_credentials_file(credentials_path)
        except ValueError as exc:
            parser.error(f"无法读取凭证文件 {credentials_path}: {exc}")

    auth_token = args.token or os.getenv("E7_TOKEN") or file_credentials.get("token")
    pd_did = args.pd_did or os.getenv("E7_PD_DID") or file_credentials.get("pd_did")
    pd_dvid = args.pd_dvid or os.getenv("E7_PD_DVID") or file_credentials.get("pd_dvid")
    jsessionid = args.jsessionid or os.getenv("E7_JSESSIONID") or file_credentials.get("jsessionid")
    if not auth_token:
        parser.error(
            "必须提供 --token，或设置环境变量 E7_TOKEN，或在凭证文件中提供 token 字段"
        )
    if not pd_did or not pd_dvid:
        parser.error(
            "必须提供 --pd-did 和 --pd-dvid，或设置环境变量 E7_PD_DID / E7_PD_DVID，"
            "或在凭证文件中提供 pd_did / pd_dvid 字段"
        )

    token_user_id = get_token_user_id(auth_token)
    user_id = args.uid or os.getenv("E7_UID") or file_credentials.get("uid") or token_user_id
    if not user_id:
        parser.error("无法从 token 解出真实用户 UID，请显式提供 --uid")
    if token_user_id and str(user_id) != token_user_id:
        parser.error(f"--uid 与 token 中的 UID 不一致，token UID 为 {token_user_id}")
    return str(user_id), str(auth_token), str(pd_did), str(pd_dvid), str(jsessionid) if jsessionid else None


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    user_id, auth_token, pd_did, pd_dvid, jsessionid = resolve_credentials(args, parser)
    token_expiry = get_token_expiry(auth_token)
    if token_expiry is not None and token_expiry <= int(time.time()):
        parser.error("提供的 token 已过期，请先刷新后再运行")

    bot = EpicSevenCommunityAIO(
        user_id=user_id,
        auth_token=auth_token,
        jsessionid=jsessionid,
        pd_did=pd_did,
        pd_dvid=pd_dvid,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        timeout=args.timeout,
    )
    bot.run(
        browse_target=max(args.browse_target, 0),
        like_target=max(args.like_target, 0),
        share_target=max(args.share_target, 0),
        topic_pages=max(args.topic_pages, 1),
        topic_page_size=max(args.topic_page_size, 1),
        goods_pages=max(args.goods_pages, 1),
        goods_page_size=max(args.goods_page_size, 1),
        skip_actions=args.skip_actions,
        skip_exchange=args.skip_exchange,
    )


if __name__ == "__main__":
    main()
