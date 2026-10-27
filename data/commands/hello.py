# ctx.user, ctx.args, ctx.message, ctx.channel, ctx.is_mod
# ctx.reply("текст") — сообщение в чат
# print(...) — строка в лог программы

def run(ctx):
    who = " ".join(ctx.args) if ctx.args else ctx.user
    ctx.reply(f"Привет, {who}!")
