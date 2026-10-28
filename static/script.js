let formElem = document.getElementById("FormElem");
let inputElem = document.getElementById("TextInput");

formElem.addEventListener("submit", (ev) => {
  ev.preventDefault();
  const text = inputElem.value;
  console.log(text);
  inputElem.value = "";
  let result_data = send_user_form_data(text);
});

async function send_user_form_data(user_data) {
    const response = await fetch("/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: user_data })
    });

    if (!response.ok) {
        throw new Error("Request failed: " + response.status);
    }

    return await response.json();
}
