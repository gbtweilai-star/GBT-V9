# tools/brand_octop.sh —— 批量换名（保守：只换 UI 文案，不动代码标识符）
set -e
cd "${1:-gbt-octop}"
grep -rl --include='*.tsx' --include='*.ts' --include='*.html' \
     -e '"Octop"' -e '>Octop<' -e 'Octop ·' dashboard/src dashboard/index.html \
  | while read f; do
      sed -i.bak 's/"Octop"/BRAND.name/g; s/>Octop</>{BRAND.name}</g' "$f"
    done
sed -i.bak 's/<title>.*<\/title>/<title>GBT小土豆V9<\/title>/' dashboard/index.html
echo "改完记得人工核对 grep -rn 'Octop' dashboard/src"

make build-frontend          # 产物进后端静态目录
docker compose -f docker/docker-compose.yml up -d
# 打开 http://127.0.0.1:8088 ，标题/Logo 应已是 GBT小土豆V9
