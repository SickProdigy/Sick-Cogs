from .movies import MovieReleases

__red_end_user_data_statement__ = (
    "This cog stores guild movie release announcement settings and posted movie IDs. "
    "It does not persistently store user data or metadata."
)


async def setup(bot):
    await bot.add_cog(MovieReleases(bot))
