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

# 联动配置
TARGET_REPO = "JE668/iptv-api"
TARGET_WORKFLOW = "main.yml"  # 目标工作流文件名
TRIGGER_TOKEN = os.environ.get("PAT_TOKEN", "") # 从 Secrets 读取 PAT

# 按照要求重命名文件
SOURCE_IP_FILE = "source-ip.txt"
SOURCE_M3U_FILE = "source-m3u.txt"
RTP_DIR = "rtp"

# ===============================
# 2. 核心验证函数
# ===============================

def verify_ip_geodata(ip):
    """第一步校验：广东省 + 中国电信"""
    try:
        url = f"http://ip-api.com/json/{ip}?lang=zh-CN"
        # 增加超时和重试
        response = requests.get(url, timeout=10)
        res = response.json()
        
        if res.get("status") != "success":
            return False
            
        region = res.get("regionName", "")
        isp_info = (res.get("isp", "") + res.get("org", "")).lower()
        
        # 匹配广东 + 电信/Chinanet
        is_match = "广东" in region and any(kw in isp_info for kw in ["电信", "telecom", "chinanet", "chinatelecom"])
        return is_match
    except Exception as e:
        print(f"   ⚠️ Geo校验异常 ({ip}): {e}")
        return False

def check_udpxy_status(ip_port):
    """
    第二步校验：尝试访问 /stat 或 /status
    """
    # 部分 udpxy 极其精简，不带 User-Agent 访问更稳
    clean_headers = {"User-Agent": "Wget/1.14"} 
    paths = ["/stat", "/status", "/status/"]
    
    for path in paths:
        try:
            url = f"http://{ip_port}{path}"
            response = requests.get(url, headers=clean_headers, timeout=4, allow_redirects=False)
            if response.status_code == 200:
                text = response.text.lower()
                # 只要包含 udpxy 或 活跃链接(active) 等特征码即视为存活
                if "udpxy" in text or "stat" in text or "client" in text:
                    return True
        except:
            continue
    return False

# ===============================
# 3. 运行逻辑
# ===============================

def stage_1_fofa():
    print("📡 1. 爬取 FOFA 并进行地理筛选...")
    ips = set()
    try:
        r = requests.get(FOFA_URL, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            found = re.findall(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+)', r.text)
            ips.update(found)
        else:
            print(f"   ❌ FOFA 响应异常: {r.status_code}")
    except Exception as e:
        print(f"   ❌ FOFA 爬取失败: {e}")

    if not ips:
        return []

    print(f"   找到 {len(ips)} 个 IP，正在校验广东电信归属地...")
    geo_valid_ips = []
    for ip_port in sorted(list(ips)):
        host = ip_port.split(":")[0]
        if verify_ip_geodata(host):
            print(f"   ✅ [地理匹配]: {ip_port}")
            geo_valid_ips.append(ip_port)
        else:
            print(f"   ❌ [非广东电信]: {ip_port}")
        # 1.5s 延迟确保 ip-api 接口稳定
        time.sleep(1.5) 
    
    return geo_valid_ips

def stage_2_validate_and_save(geo_ips):
    print(f"🔍 2. 验证 /stat 接口 (共 {len(geo_ips)} 个候选)...")
    final_ips = []

    if not geo_ips:
        return []

    # 多线程验证接口
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
        future_to_ip = {executor.submit(check_udpxy_status, ip): ip for ip in geo_ips}
        for future in concurrent.futures.as_completed(future_to_ip):
            ip_port = future_to_ip[future]
            if future.result():
                print(f"   🟢 [接口在线]: {ip_port}")
                final_ips.append(ip_port)
            else:
                print(f"   🔴 [接口下线]: {ip_port}")

    if final_ips:
        # 写入 source-ip.txt
        final_ips.sort()
        with open(SOURCE_IP_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(final_ips))
        print(f"✅ {SOURCE_IP_FILE} 已保存 ({len(final_ips)} 条)")
    else:
        print("❌ 接口验证环节未发现任何在线 IP")
        
    return final_ips

def stage_3_combine(final_ips):
    print("🧩 3. 正在生成拼装列表 source-m3u.txt...")
    if not final_ips:
        return

    combined = []
    # 查找模板文件，这里寻找任何以广东电信命名的txt
    rtp_file = os.path.join(RTP_DIR, "广东电信.txt")
    if not os.path.exists(rtp_file):
        print(f"   ⚠️ 模板文件 {rtp_file} 不存在，无法生成 m3u 列表")
        return

    with open(rtp_file, encoding="utf-8") as f:
        rtp_lines = [x.strip() for x in f if "," in x]

    for ip in final_ips:
        for rtp in rtp_lines:
            name, rtp_url = rtp.split(",", 1)
            if "://" not in rtp_url: continue
            proto = "rtp" if "rtp://" in rtp_url else "udp"
            suffix = rtp_url.split("://")[1]
            combined.append(f"{name},http://{ip}/{proto}/{suffix}")

    if combined:
        with open(SOURCE_M3U_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(list(set(combined))))
        print(f"✅ {SOURCE_M3U_FILE} 已保存 ({len(combined)} 条)")

def trigger_remote_action():
    """触发目标仓库的 main.yml"""
    if not TRIGGER_TOKEN:
        print("⚠️ 未发现 PAT_TOKEN，联动跳过。")
        return
    
    # 根据你的检查结果，这里可以填 "main" 或 "master"
    # 如果不确定，通常报错 "No ref found" 就是因为分支名对不上
    target_branch = "main" 
    
    print(f"🚀 正在触发 {TARGET_REPO} 的 {TARGET_WORKFLOW} (分支: {target_branch})...")
    url = f"https://api.github.com/repos/{TARGET_REPO}/actions/workflows/{TARGET_WORKFLOW}/dispatches"
    
    headers = {
        "Authorization": f"token {TRIGGER_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Python-Request" # 增加 UA 提高兼容性
    }
    
    data = {"ref": target_branch} 
    
    try:
        r = requests.post(url, headers=headers, json=data)
        
        # 成功状态码是 204
        if r.status_code == 204:
            print("🎉 成功：目标仓库 Action 已被激活！")
        elif r.status_code == 422:
            print(f"❌ 触发失败 (422)：分支名 '{target_branch}' 可能不对，或者目标 YAML 没开 workflow_dispatch。")
            # 自动尝试一次 master
            if target_branch == "main":
                print("🔄 尝试切换分支为 'master' 再次触发...")
                data["ref"] = "master"
                r2 = requests.post(url, headers=headers, json=data)
                if r2.status_code == 204:
                    print("🎉 成功：通过 'master' 分支激活成功！")
                else:
                    print(f"❌ 最终失败：{r2.status_code}, {r2.text}")
        else:
            print(f"❌ 触发失败：{r.status_code}, {r.text}")
    except Exception as e:
        print(f"❌ 联动异常：{e}")


def push():
    print("⬆️ 同步到 GitHub...")
    os.system("git config --global user.name 'github-actions[bot]'")
    os.system("git config --global user.email 'github-actions[bot]@users.noreply.github.com'")
    os.system("git add source-ip.txt source-m3u.txt")
    os.system("git commit -m 'Update source IPs and M3U files' || echo 'No changes'")
    os.system("git push origin main")

# ===============================
# 主程序
# ===============================
if __name__ == "__main__":
    # 1. 地理筛选
    candidate_list = stage_1_fofa()
    
    if candidate_list:
        # 2. 接口状态验证并保存 source-ip.txt
        online_list = stage_2_validate_and_save(candidate_list)
        
        if online_list:
            # 3. 拼装生成 source-m3u.txt
            stage_3_combine(online_list)
            # 4. 推送
            push()
            # 只有在本地推送成功后才去触发远程
            trigger_remote_action()
        else:
            print("❌ 验证结果为空，不执行推送。")
    else:
        print("❌ 地理筛选结果为空，请检查 FOFA 搜索或地理 API。")
