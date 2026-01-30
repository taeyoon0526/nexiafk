from .afkmention import AFKMention


async def setup(bot):
    await bot.add_cog(AFKMention(bot))

