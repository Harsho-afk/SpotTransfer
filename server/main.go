package main

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"log"
	"net/http"
	"os"
	"strings"

	"github.com/gofiber/fiber/v2"
	"github.com/gofiber/fiber/v2/middleware/cors"
	"github.com/gofiber/fiber/v2/middleware/session"
	"github.com/gofiber/storage/memory"
	"github.com/joho/godotenv"
	"github.com/zmb3/spotify/v2"
	"golang.org/x/oauth2"
	oauth2spotify "golang.org/x/oauth2/spotify"
)

var (
	oauthConfig *oauth2.Config
	store       *session.Store
)

func main() {
	err := godotenv.Load()
	if err != nil {
		log.Fatal("Error loading .env file")
	}

	oauthConfig = &oauth2.Config{
		RedirectURL:  os.Getenv("S_REDIRECT"),
		ClientID:     os.Getenv("S_ID"),
		ClientSecret: os.Getenv("S_SECRET"),
		Scopes:       []string{"playlist-read-private"},
		Endpoint:     oauth2spotify.Endpoint,
	}

	store = session.New(session.Config{
		Storage: memory.New(),
	})

	app := fiber.New()

	app.Use(cors.New(cors.Config{
		AllowOrigins:     "http://localhost:5173",
		AllowHeaders:     "Origin, Content-Type, Accept, Authorization",
		AllowMethods:     "GET,POST,OPTIONS",
		AllowCredentials: true,
	}))

	app.Get("/login", loginHandler)
	app.Get("/callback", callbackHandler)
	app.Get("/api/playlist", getPlaylistHandler)
	app.Post("/logout", logoutHandler)

	log.Fatal(app.Listen(":8080"))
}

func generateState() string {
	b := make([]byte, 16)
	rand.Read(b)
	return base64.URLEncoding.EncodeToString(b)
}

func loginHandler(c *fiber.Ctx) error {
	url := oauthConfig.AuthCodeURL(generateState())
	return c.Redirect(url)
}

func callbackHandler(c *fiber.Ctx) error {
	session, err := store.Get(c)
	if err != nil {
		return c.Status(http.StatusInternalServerError).SendString("Failed to get session")
	}

	token, err := oauthConfig.Exchange(context.Background(), c.Query("code"))
	if err != nil {
		return c.Status(http.StatusUnauthorized).SendString("Failed to exchange token")
	}

	session.Set("spotify_token", token.AccessToken)
	if err := session.Save(); err != nil {
		return c.Status(http.StatusInternalServerError).SendString("Failed to save session")
	}

	redirectURL := "http://localhost:5173/?token=" + token.AccessToken
	return c.Redirect(redirectURL)
}

func getPlaylistHandler(c *fiber.Ctx) error {
	session, err := store.Get(c)
	if err != nil {
		return c.Status(http.StatusInternalServerError).SendString("Failed to get session")
	}

	token := session.Get("spotify_token")
	if token == nil {
		return c.Status(http.StatusUnauthorized).SendString("Please login first")
	}

	playlistLink := c.Query("link")
	if playlistLink == "" {
		return c.Status(http.StatusBadRequest).SendString("Playlist link is required")
	}

	parts := strings.Split(playlistLink, "/")
	lastPart := parts[len(parts)-1]
	playlistID := strings.Split(lastPart, "?")[0]

	tokenObj := &oauth2.Token{AccessToken: token.(string)}
	authClient := oauthConfig.Client(context.Background(), tokenObj)
	client := spotify.New(authClient)

	var songs []map[string]any
	var offset = 0
	const limit = 100

	for {
		playlist, err := client.GetPlaylistItems(context.Background(), spotify.ID(playlistID), spotify.Offset(offset), spotify.Limit(limit))
		if err != nil {
			return c.Status(http.StatusInternalServerError).SendString("Failed to get playlist tracks")
		}

		for _, item := range playlist.Items {
			// if item.IsLocal {
			// 	continue
			// }
			var imageURL string
			if len(item.Track.Track.Album.Images[1].URL) > 0 {
				imageURL = item.Track.Track.Album.Images[1].URL
			}

			var artistNames []string
			for _, artist := range item.Track.Track.Artists {
				artistNames = append(artistNames, artist.Name)
			}

			songs = append(songs, map[string]any{
				"name":   item.Track.Track.Name,
				"artist": strings.Join(artistNames, ", "),
				"album":  item.Track.Track.Album.Name,
				"image":  imageURL,
			})
		}

		if len(playlist.Items) < limit {
			break
		}

		offset += limit
	}

	return c.JSON(songs)
}

func logoutHandler(c *fiber.Ctx) error {
	session, err := store.Get(c)
	if err != nil {
		return c.Status(http.StatusInternalServerError).SendString("Failed to get session")
	}

	if err := session.Destroy(); err != nil {
		return c.Status(http.StatusInternalServerError).SendString("Failed to destroy session")
	}

	c.ClearCookie()
	return c.SendStatus(http.StatusOK)
}
