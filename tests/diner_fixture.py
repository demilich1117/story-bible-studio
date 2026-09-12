"""Neutral, isolated UI QA fixture. Uses real core operations, never fake events."""
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.agents/skills/story-bible-studio/scripts'))
from new_story_project import create_project
from studio_core import create_session, commit_turn, add_variant, create_checkpoint, load_yaml, write_yaml


def create_demo(root):
    project=create_project(root/'作品','雨夜公路 · Route 66',anchors=['June'])
    cfg=load_yaml(project/'项目配置.yaml');cfg['nsfw']['enabled']=False;write_yaml(project/'项目配置.yaml',cfg)
    for path in (project/'StoryBible').glob('*.md'):
        path.write_text(f'# {path.stem}\n\n一间公路旁的通宵餐馆。六月是老板，记得每位熟客的饮料偏好。\n',encoding='utf-8')
    (project/'StoryBible/角色/June.md').write_text('# June（六月）\n\n## 基本身份\n\n32 岁的餐馆老板。\n\n## 人格核心\n\n说话直接，关心别人时会先递一杯水。',encoding='utf-8')
    (project/'StoryBible/语料/June.md').write_text('# 声线\n\n## 核心声线\n\n短句，偶尔用食物打比方，不过问陌生人的秘密。',encoding='utf-8')
    create_session(project,'返程的雨')
    create_session(project,'先别关灯')
    session=create_session(project,'午夜后的第七杯可乐')
    commit_turn(session,'我推开餐馆的门，收起滴水的伞。“还来得及点餐吗？”',
        '门上的铜铃响了两声，第二声轻得几乎被雨盖住。\n\n柜台后的女人抬起头，铅笔还夹在指间。她先看了一眼你脚边的小片水迹，又看了看墙上那只慢了七分钟的钟。\n\n“来得及。”六月把倒扣的杯子翻过来，“厨房还热着。靠窗的位置没人，你可以看着雨吃。”\n\n她沿着柜台推来一份菜单。纸角已经磨软，双层芝士汉堡旁边画着一颗有点歪的星星。',
        status='时间：周五 23:47\n地点：66 号公路旁的餐馆\n在场：你、六月',scene_patch={'location':'路边餐馆','characters':['June']})
    commit_turn(session,'“那就一个双层芝士汉堡。”我在窗边坐下，“还有可乐，多一点冰。”',
        '六月在小票上写下你的单子，撕纸声干脆得像给这场漫长的雨划了一道句号。\n\n“芝士要两片，对吧？”\n\n她没有等菜单替你回答，抬眼看了过来。玻璃窗上倒映着柜台的暖光，窗外偶尔有车驶过，车灯把雨丝照成一条条细亮的线。\n\n很快，一杯可乐落在桌面上。冰块轻轻碰着杯壁，吸管的纸套还留着半截。她把一小碟薯条放在旁边，又把番茄酱往你这边挪了一点。\n\n“汉堡还得等几分钟。这个先吃，算我请的。”\n\n她的手在桌沿停了一下。\n\n“开了很远？”',
        status='时间：周五 23:51\n地点：临窗卡座\n物品：可乐、薯条、折起的菜单',scene_patch={'location':'临窗卡座'})
    add_variant(session,2,'“好口味。”六月把小票夹在出餐口，转身去拿杯子。\n\n她先放冰，再倒可乐。泡沫升到杯沿前恰好停住。\n\n“薯条想要脆一点，还是软一点？”她问。',scene_patch={'location':'临窗卡座'})
    create_checkpoint(session,'落座之后',1)
    return project


if __name__=='__main__':
    demo=ROOT/'.workbench/demo'
    if not (demo/'作品').exists():create_demo(demo)
    print(demo)
