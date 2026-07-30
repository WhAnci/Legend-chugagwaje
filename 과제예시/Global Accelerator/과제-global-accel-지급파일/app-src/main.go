// arena — 매치메이킹 노드 (연습과제 지급 바이너리)
// 참고용 소스. 선수는 빌드·수정할 필요 없이 제공된 바이너리를 그대로 사용한다.
package main

import (
	"encoding/json"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"time"
)

const imds = "http://169.254.169.254"

// IMDSv2 로 인스턴스 메타데이터를 읽는다. 실패하면 빈 문자열.
func meta(path string) string {
	c := &http.Client{Timeout: 2 * time.Second}
	req, _ := http.NewRequest(http.MethodPut, imds+"/latest/api/token", nil)
	req.Header.Set("X-aws-ec2-metadata-token-ttl-seconds", "60")
	res, err := c.Do(req)
	if err != nil {
		return ""
	}
	tok, _ := io.ReadAll(res.Body)
	res.Body.Close()

	req, _ = http.NewRequest(http.MethodGet, imds+"/latest/meta-data/"+path, nil)
	req.Header.Set("X-aws-ec2-metadata-token", string(tok))
	res, err = c.Do(req)
	if err != nil || res.StatusCode != 200 {
		return ""
	}
	defer res.Body.Close()
	b, _ := io.ReadAll(res.Body)
	return string(b)
}

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	// NODE_NAME 은 태스크/인스턴스 구분용 라벨. 미설정 시 호스트명.
	node := os.Getenv("NODE_NAME")
	if node == "" {
		node, _ = os.Hostname()
	}

	az := meta("placement/availability-zone")
	region := os.Getenv("REGION")
	if region == "" {
		region = meta("placement/region")
	}
	if region == "" && len(az) > 1 {
		region = az[:len(az)-1]
	}
	instance := meta("instance-id")

	healthy := true

	mux := http.NewServeMux()

	// 로드밸런서 헬스체크 대상.
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		if !healthy {
			w.WriteHeader(http.StatusServiceUnavailable)
			io.WriteString(w, "unhealthy\n")
			return
		}
		w.WriteHeader(http.StatusOK)
		io.WriteString(w, "ok\n")
	})

	// 어느 노드가 응답했는지 식별한다.
	mux.HandleFunc("/whoami", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{
			"region":   region,
			"az":       az,
			"instance": instance,
			"node":     node,
		})
	})

	// 매치 요청. 라운드트립 확인용 에코.
	mux.HandleFunc("/match", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{
			"matched": true,
			"region":  region,
			"node":    node,
			"peer":    remoteIP(r),
			"ts":      time.Now().UTC().Format(time.RFC3339),
		})
	})

	// 장애 시뮬레이션 — 이 노드를 헬스체크 실패 상태로 만든다.
	mux.HandleFunc("/drain", func(w http.ResponseWriter, r *http.Request) {
		healthy = false
		w.WriteHeader(http.StatusOK)
		io.WriteString(w, "draining\n")
	})
	mux.HandleFunc("/restore", func(w http.ResponseWriter, r *http.Request) {
		healthy = true
		w.WriteHeader(http.StatusOK)
		io.WriteString(w, "restored\n")
	})

	log.Printf("arena listening on :%s region=%s node=%s", port, region, node)
	log.Fatal(http.ListenAndServe(":"+port, mux))
}

func remoteIP(r *http.Request) string {
	h, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return r.RemoteAddr
	}
	return h
}
