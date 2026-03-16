import React, { useMemo, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import * as DocumentPicker from "expo-document-picker";
import * as FileSystem from "expo-file-system";
import * as Sharing from "expo-sharing";

const API_BASE_URL = "http://127.0.0.1:8010/api";

async function apiFetch(path, token, options = {}) {
  const headers = {
    ...(options.headers || {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
  const response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const message = typeof body === "string" ? body : body.error;
    throw new Error(message || "Request failed.");
  }
  return body;
}

export default function App() {
  const [token, setToken] = useState("");
  const [email, setEmail] = useState("admin@westdealshul.org");
  const [password, setPassword] = useState("ChangeMe123!");
  const [jobs, setJobs] = useState([]);
  const [selectedJob, setSelectedJob] = useState(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [rabbiName, setRabbiName] = useState("");
  const [topic, setTopic] = useState("");
  const [transcriptText, setTranscriptText] = useState("");
  const [pamphletText, setPamphletText] = useState("");
  const [audioAsset, setAudioAsset] = useState(null);
  const [editedTopic, setEditedTopic] = useState("");
  const [editedPamphlet, setEditedPamphlet] = useState("");
  const [pdfLineSpacing, setPdfLineSpacing] = useState("1.0");
  const [pdfFontSize, setPdfFontSize] = useState("0");
  const [pdfBackgroundMode, setPdfBackgroundMode] = useState("default");
  const [reviewAnswers, setReviewAnswers] = useState({});
  const [backgroundAsset, setBackgroundAsset] = useState(null);

  const isLoggedIn = useMemo(() => Boolean(token), [token]);

  async function login() {
    try {
      setLoading(true);
      setMessage("");
      const data = await apiFetch("/auth/login", "", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      setToken(data.token);
      setMessage("Logged in.");
      await refreshJobs(data.token);
    } catch (error) {
      setMessage(error.message);
    } finally {
      setLoading(false);
    }
  }

  async function refreshJobs(activeToken = token) {
    try {
      setLoading(true);
      const data = await apiFetch("/jobs", activeToken);
      setJobs(data.jobs || []);
    } catch (error) {
      setMessage(error.message);
    } finally {
      setLoading(false);
    }
  }

  async function openJob(jobId) {
    try {
      setLoading(true);
      const data = await apiFetch(`/jobs/${jobId}`, token);
      setSelectedJob(data);
      setEditedTopic(data.topic || "");
      setEditedPamphlet(data.edited_one_pager || data.one_pager || "");
      setPdfLineSpacing(String(data.pdf_line_spacing || 1.0));
      setPdfFontSize(String(data.pdf_font_size || 0));
      setPdfBackgroundMode(data.pdf_background_mode || "default");
      const answers = {};
      (data.review_items || []).forEach((item) => {
        answers[item.id] = item.clarification || "";
      });
      setReviewAnswers(answers);
    } catch (error) {
      setMessage(error.message);
    } finally {
      setLoading(false);
    }
  }

  async function pickAudio() {
    const result = await DocumentPicker.getDocumentAsync({
      type: ["audio/mpeg", "video/mp4"],
      copyToCacheDirectory: true,
    });
    if (!result.canceled) {
      setAudioAsset(result.assets[0]);
    }
  }

  async function pickBackground() {
    const result = await DocumentPicker.getDocumentAsync({
      type: ["image/png", "image/jpeg"],
      copyToCacheDirectory: true,
    });
    if (!result.canceled) {
      setBackgroundAsset(result.assets[0]);
      setPdfBackgroundMode("custom");
    }
  }

  async function createJob() {
    try {
      setLoading(true);
      setMessage("");
      const formData = new FormData();
      formData.append("rabbi_name", rabbiName);
      formData.append("topic", topic);
      if (transcriptText.trim()) {
        formData.append("transcript_text", transcriptText);
      }
      if (pamphletText.trim()) {
        formData.append("pamphlet_text", pamphletText);
      }
      if (audioAsset) {
        formData.append("audio", {
          uri: audioAsset.uri,
          name: audioAsset.name,
          type: audioAsset.mimeType || (audioAsset.name.endsWith(".mp4") ? "video/mp4" : "audio/mpeg"),
        });
      }
      const data = await apiFetch("/jobs", token, {
        method: "POST",
        body: formData,
      });
      setMessage(`Job created: ${data.job_id}`);
      setRabbiName("");
      setTopic("");
      setTranscriptText("");
      setPamphletText("");
      setAudioAsset(null);
      await refreshJobs();
      await openJob(data.job_id);
    } catch (error) {
      setMessage(error.message);
    } finally {
      setLoading(false);
    }
  }

  async function saveJobEdits() {
    if (!selectedJob) return;
    try {
      setLoading(true);
      if (pdfBackgroundMode === "custom" && backgroundAsset) {
        const formData = new FormData();
        formData.append("background", {
          uri: backgroundAsset.uri,
          name: backgroundAsset.name,
          type: backgroundAsset.mimeType || "image/jpeg",
        });
        await apiFetch(`/jobs/${selectedJob.id}/background`, token, {
          method: "POST",
          body: formData,
        });
      }
      await apiFetch(`/jobs/${selectedJob.id}`, token, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          topic: editedTopic,
          edited_one_pager: editedPamphlet,
          pdf_line_spacing: parseFloat(pdfLineSpacing || "1.0"),
          pdf_font_size: parseFloat(pdfFontSize || "0"),
          pdf_background_mode: pdfBackgroundMode,
        }),
      });
      setMessage("Job saved.");
      setBackgroundAsset(null);
      await openJob(selectedJob.id);
      await refreshJobs();
    } catch (error) {
      setMessage(error.message);
    } finally {
      setLoading(false);
    }
  }

  async function downloadAndShare(kind) {
    if (!selectedJob) return;
    try {
      setLoading(true);
      const detail = await apiFetch(`/jobs/${selectedJob.id}`, token);
      const fallbackName = kind === "pdf" ? `${detail.topic || "pamphlet"}.pdf` : `${detail.topic || "pamphlet"}.docx`;
      const fileName = fallbackName.replace(/[<>:"/\\|?*]+/g, "_");
      const fileUri = `${FileSystem.cacheDirectory}${fileName}`;
      await FileSystem.downloadAsync(
        `${API_BASE_URL}/jobs/${selectedJob.id}/${kind}`,
        fileUri,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (await Sharing.isAvailableAsync()) {
        await Sharing.shareAsync(fileUri);
      }
    } catch (error) {
      setMessage(error.message);
    } finally {
      setLoading(false);
    }
  }

  async function submitReview() {
    if (!selectedJob) return;
    try {
      setLoading(true);
      await apiFetch(`/jobs/${selectedJob.id}/review`, token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answers: reviewAnswers }),
      });
      setMessage("Review submitted.");
      await openJob(selectedJob.id);
      await refreshJobs();
    } catch (error) {
      setMessage(error.message);
    } finally {
      setLoading(false);
    }
  }

  if (!isLoggedIn) {
    return (
      <SafeAreaView style={styles.container}>
        <Text style={styles.title}>Parasha One-Pager Mobile</Text>
        <TextInput style={styles.input} value={email} onChangeText={setEmail} placeholder="Email" autoCapitalize="none" />
        <TextInput style={styles.input} value={password} onChangeText={setPassword} placeholder="Password" secureTextEntry />
        <TouchableOpacity style={styles.button} onPress={login}>
          <Text style={styles.buttonText}>Log In</Text>
        </TouchableOpacity>
        {loading ? <ActivityIndicator /> : null}
        {message ? <Text style={styles.message}>{message}</Text> : null}
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.container}>
      <ScrollView contentContainerStyle={styles.scroll}>
        <Text style={styles.title}>Parasha One-Pager Mobile</Text>
        {loading ? <ActivityIndicator /> : null}
        {message ? <Text style={styles.message}>{message}</Text> : null}

        <View style={styles.card}>
          <Text style={styles.sectionTitle}>Create Job</Text>
          <TextInput style={styles.input} value={rabbiName} onChangeText={setRabbiName} placeholder="Rabbi name" />
          <TextInput style={styles.input} value={topic} onChangeText={setTopic} placeholder="Class title / topic" />
          <TextInput style={[styles.input, styles.largeInput]} value={transcriptText} onChangeText={setTranscriptText} placeholder="Paste transcript (optional)" multiline />
          <TextInput style={[styles.input, styles.largeInput]} value={pamphletText} onChangeText={setPamphletText} placeholder="Paste finished pamphlet (optional)" multiline />
          <TouchableOpacity style={styles.secondaryButton} onPress={pickAudio}>
            <Text style={styles.secondaryButtonText}>{audioAsset ? audioAsset.name : "Choose .mp3 or .mp4"}</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.button} onPress={createJob}>
            <Text style={styles.buttonText}>Submit</Text>
          </TouchableOpacity>
        </View>

        <View style={styles.card}>
          <View style={styles.row}>
            <Text style={styles.sectionTitle}>Jobs</Text>
            <TouchableOpacity onPress={() => refreshJobs()}>
              <Text style={styles.link}>Refresh</Text>
            </TouchableOpacity>
          </View>
          <FlatList
            data={jobs}
            keyExtractor={(item) => item.id}
            scrollEnabled={false}
            renderItem={({ item }) => (
              <TouchableOpacity style={styles.jobRow} onPress={() => openJob(item.id)}>
                <Text style={styles.jobTitle}>{item.topic}</Text>
                <Text style={styles.jobMeta}>{item.status} • {item.rabbi_name}</Text>
              </TouchableOpacity>
            )}
          />
        </View>

        {selectedJob ? (
          <View style={styles.card}>
            <Text style={styles.sectionTitle}>Selected Job</Text>
            <Text style={styles.jobMeta}>{selectedJob.status} • {selectedJob.message}</Text>
            {selectedJob.status === "needs_review" ? (
              <>
                {(selectedJob.review_items || []).map((item) => (
                  <View key={item.id} style={styles.reviewItem}>
                    <Text style={styles.jobTitle}>{item.raw_text}</Text>
                    <Text style={styles.jobMeta}>{item.context}</Text>
                    <TextInput
                      style={styles.input}
                      value={reviewAnswers[item.id] || ""}
                      onChangeText={(value) => setReviewAnswers((current) => ({ ...current, [item.id]: value }))}
                      placeholder="Clarify transliteration + English"
                    />
                  </View>
                ))}
                <TouchableOpacity style={styles.button} onPress={submitReview}>
                  <Text style={styles.buttonText}>Submit Review</Text>
                </TouchableOpacity>
              </>
            ) : null}
            <TextInput style={styles.input} value={editedTopic} onChangeText={setEditedTopic} placeholder="Pamphlet title" />
            <TextInput style={[styles.input, styles.editor]} multiline value={editedPamphlet} onChangeText={setEditedPamphlet} placeholder="Pamphlet text" />
            <TextInput style={styles.input} value={pdfLineSpacing} onChangeText={setPdfLineSpacing} placeholder="PDF line spacing" />
            <TextInput style={styles.input} value={pdfFontSize} onChangeText={setPdfFontSize} placeholder="PDF font size (0 for auto)" />
            <TextInput style={styles.input} value={pdfBackgroundMode} onChangeText={setPdfBackgroundMode} placeholder="default | blank | custom" />
            <TouchableOpacity style={styles.secondaryButton} onPress={pickBackground}>
              <Text style={styles.secondaryButtonText}>{backgroundAsset ? backgroundAsset.name : "Choose custom background"}</Text>
            </TouchableOpacity>
            <View style={styles.row}>
              <TouchableOpacity style={styles.button} onPress={saveJobEdits}>
                <Text style={styles.buttonText}>Save</Text>
              </TouchableOpacity>
              <TouchableOpacity style={styles.secondaryButton} onPress={() => downloadAndShare("pdf")}>
                <Text style={styles.secondaryButtonText}>PDF</Text>
              </TouchableOpacity>
              <TouchableOpacity style={styles.secondaryButton} onPress={() => downloadAndShare("docx")}>
                <Text style={styles.secondaryButtonText}>DOCX</Text>
              </TouchableOpacity>
            </View>
          </View>
        ) : null}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#f4f1e8" },
  scroll: { padding: 16, gap: 16 },
  title: { fontSize: 28, fontWeight: "700", color: "#5a3a1f", marginBottom: 12 },
  card: { backgroundColor: "#fffdf7", borderRadius: 14, padding: 16, gap: 12 },
  sectionTitle: { fontSize: 20, fontWeight: "700", color: "#5a3a1f" },
  input: { backgroundColor: "white", borderWidth: 1, borderColor: "#d9c9b4", borderRadius: 10, padding: 12 },
  largeInput: { minHeight: 110, textAlignVertical: "top" },
  editor: { minHeight: 220, textAlignVertical: "top" },
  button: { backgroundColor: "#8a5a2f", borderRadius: 10, padding: 12, alignItems: "center" },
  buttonText: { color: "white", fontWeight: "700" },
  secondaryButton: { backgroundColor: "#efe7d7", borderRadius: 10, padding: 12, alignItems: "center" },
  secondaryButtonText: { color: "#5a3a1f", fontWeight: "700" },
  message: { color: "#7c2d12" },
  row: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 8 },
  link: { color: "#3f5f4a", fontWeight: "700" },
  jobRow: { paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: "#e5dccd" },
  jobTitle: { fontSize: 16, fontWeight: "700", color: "#1f2933" },
  jobMeta: { color: "#5f6b76" },
  reviewItem: { gap: 8, paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: "#e5dccd" },
});
