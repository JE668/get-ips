import os
import re
import requests
import time
import concurrent.futures

# ===============================
# 1. 配置区
# ===============================
FOFA_URL = "https://fofa.info/result?qbase64=IlVEUFhZIiAmJiBjb3VudHJ5PSJDTiIgJiYgcmVnaW9uPSJHdWFuZ2RvbmciICYmIGNpdHk9Ilpob25nc2hhbiI%3D"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Cookie": os.environ.get("FOFA_COOKIE", "") 
}

IP_DIR = "ip"
RTP_DIR = "rtp"
ZUBO_FILE = "zubo.txt"
SOURCE_FILE = "source.txt"

# ===============================
# 2. 核心验证函数
# ===============================

def verify_ip_geodata(ip):
    """第一步校验：广东省 + 中国电信"""
    try:
        url = f"http://ip-api.com/json/{ip}?lang=zh-CN"
        res = requests.get(url, timeout=10).json()
        if res.get("status") != "success":
            return False
        region = res.get("regionName", "")
        isp_info = (res.get("isp", "") + res.get("org", "")).lower()
        return "广东" in region and any(kw in isp_info for kw in ["电信", "telecom", "chinanet", "chinatelecom"])
    except:
        return False

def check_udpxy_status(ip_port):
    """
    第二步校验：尝试访问 /stat 或 /status
    如果返回 200 OK 且包含 udpxy 关键字，则判定服务在线
    """
    paths = ["/stat", "/status"]
    for path in paths:
        try:
            url = f"http://{ip_port}{path}"
            # 设置较短的超时，UDPXY 响应通常很快
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                # 进一步检查内容，确保是 udpxy 页面
                if "udpxy" in response.text.lower() or "status" in response.text.lower():
                    return True
        except:
            continue
    return False

# ===============================
# 3. 运行逻辑
# ===============================

def stage_1_fofa():
    """爬取并初步筛选地理位置"""
    print("📡 1. 爬取 FOFA 并校验归属地 (广东电信)...")
    ips = set()
    try:
        r = requests.get(FOFA_URL, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            found = re.findall(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+)', r.text)
            ips.update(found)
    except Exception as e:
        print(f"❌ FOFA 爬取失败: {e}")

    geo_valid_ips = []
    for ip_port in sorted(list(ips)):
        host = ip_port.split(":")[0]
        if verify_ip_geodata(host):
            print(f"   [地理通过]: {ip_port}")
            geo_valid_ips.append(ip_port)
        time.sleep(1.2) # 防止 ip-api 封禁
    
    return geo_valid_ips

def stage_3_validate_and_output(geo_ips):
    """多线程验证 UDPXY 状态页面并输出"""
    print(f"🔍 2. 验证 /stat 接口状态 (共 {len(geo_ips)} 个候选)...")
    final_ips = []

    # 使用多线程加快 Web 接口验证速度
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_to_ip = {executor.submit(check_udpxy_status, ip): ip for ip in geo_ips}
        for future in concurrent.futures.as_completed(future_to_ip):
            ip_port = future_to_ip[future]
            try:
                if future.result():
                    print(f"   ✅ [接口在线]: {ip_port}")
                    final_ips.append(ip_port)
                else:
                    print(f"   ❌ [接口离线]: {ip_port}")
            except:
                pass

    # 保存地理通过且接口在线的 IP 进 ip/ 目录
    os.makedirs(IP_DIR, exist_ok=True)
    with open(os.path.join(IP_DIR, "广东电信.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(final_ips)))

    # 输出 source.txt
    with open(SOURCE_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(final_ips)))
    
    print(f"✅ {SOURCE_FILE} 已更新，共 {len(final_ips)} 个服务在线")
    return final_ips

def stage_2_combine(final_ips):
    """组合模板生成 zubo.txt (仅针对在线 IP)"""
    print("🧩 3. 正在生成 zubo.txt...")
    combined = []
    rtp_file = os.path.join(RTP_DIR, "广东电信.txt")
    if not os.path.exists(rtp_file): return

    with open(rtp_file, encoding="utf-8") as f:
        rtp_lines = [x.strip() for x in f if "," in x]

    for ip in final_ips:
        for rtp in rtp_lines:
            name, rtp_url = rtp.split(",", 1)
            if "://" not in rtp_url: continue
            proto = "rtp" if "rtp://" in rtp_url else "udp"
            suffix = rtp_url.split("://")[1]
            combined.append(f"{name},http://{ip}/{proto}/{suffix}")

    with open(ZUBO_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(list(set(combined))))

def push():
    """同步到 GitHub"""
    os.system("git config --global user.name 'github-actions[bot]'")
    os.system("git config --global user.email 'github-actions[bot]@users.noreply.github.com'")
    os.system(f"git add .")
    os.system("git commit -m 'Update source.txt with validated udpxy hosts' || echo 'No changes'")
    os.system("git push origin main")

# ===============================
# 主程序
# ===============================
if __name__ == "__main__":
    # 1. 地理筛选
    geo_list = stage_1_fofa()
    
    if geo_list:
        # 2. 接口状态筛选并输出 source.txt
        online_list = stage_3_validate_and_output(geo_list)
        
        if online_list:
            # 3. 生成完整 zubo.txt
            stage_2_combine(online_list)
            # 4. 推送
            push()
        else:
            print("❌ 接口验证全部失败，没有在线的 UDPXY 服务。")
    else:
        print("❌ 未发现符合条件的广东电信 IP。")
