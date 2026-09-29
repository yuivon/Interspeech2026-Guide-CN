"""对已采集论文生成可断点续跑的中文结构化分析。"""
import argparse
from pipeline import analyze

parser = argparse.ArgumentParser()
parser.add_argument("--limit", type=int, help="最多处理多少篇尚未分析或来源已变化的论文")
args = parser.parse_args()
analyze(args.limit)
