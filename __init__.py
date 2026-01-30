from .nexiafk import NexiAFK


async def setup(bot):
    await bot.add_cog(NexiAFK(bot))

