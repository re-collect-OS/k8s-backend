# -*- coding: utf-8 -*-


def headers() -> dict[str, str]:
    user_agent = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:88.0)"
        " Gecko/20100101 Firefox/88.0"
    )
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Max-Age": "3600",
        "User-Agent": user_agent,
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
    }
