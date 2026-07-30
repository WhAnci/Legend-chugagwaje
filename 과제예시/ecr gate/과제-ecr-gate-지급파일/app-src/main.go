// shopd — 주문 API (연습과제 지급 바이너리)
// 참고용 소스. 선수는 빌드·수정할 필요 없이 제공된 바이너리를 그대로 사용한다.
package main

import (
	"encoding/json"
	"io"
	"log"
	"net/http"
	"os"
	"time"
)

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	// 이미지 빌드 시 주입되는 라벨. 어떤 이미지가 실행 중인지 식별한다.
	build := os.Getenv("BUILD")
	if build == "" {
		build = "unknown"
	}

	mux := http.NewServeMux()

	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		io.WriteString(w, "ok\n")
	})

	mux.HandleFunc("/version", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{
			"app":   "shopd",
			"build": build,
		})
	})

	mux.HandleFunc("/orders", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{
			"orders": []map[string]any{
				{"id": "A-1001", "item": "keyboard", "qty": 1},
				{"id": "A-1002", "item": "monitor", "qty": 2},
			},
			"build": build,
			"ts":    time.Now().UTC().Format(time.RFC3339),
		})
	})

	log.Printf("shopd listening on :%s build=%s", port, build)
	log.Fatal(http.ListenAndServe(":"+port, mux))
}
